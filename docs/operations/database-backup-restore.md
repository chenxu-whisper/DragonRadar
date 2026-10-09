# Database Backup And Restore Rollback

Production rollback MUST use a verified backup restored into a distinct database.
Production rollback must NEVER use `alembic downgrade`. Existing downgrade
functions are historical development artifacts, not an operations mechanism.

`RPO <= 1 hour` and `RTO <= 4 hours` are suggested, unapproved targets. Record
measured results; do not report either target as an approved service objective.

## Preconditions

1. Identify the incident, operator, application version, database name, current
   Alembic revision, and intended backup ID.
2. Confirm sufficient storage for the custom-format backup and a distinct
   restore database.
3. Confirm the previous application artifact is available, but do not deploy it.
4. Open an audit record and assign one operator to control the write stop.

## Stop Writes And Back Up

1. Stop writes at every application and worker entry point. Leave reads
   available only when they cannot enqueue or mutate data.
2. Enforce the database write freeze before fingerprinting or dumping. Set
   `default_transaction_read_only=on` for the validated, safely quoted source
   database identifier, terminate other source sessions, verify none remain,
   reconnect, and prove `transaction_read_only=on`. Perform this drill only in
   a dedicated test container; production operators must use the approved
   equivalent controls.
3. Record the UTC write-stop time, capture the full stable source fingerprint,
   and start the write-freeze timer.
4. Create the custom-format backup without printing credentials:

   ```bash
   docker exec "$POSTGRES_CONTAINER" pg_dump \
     --username "$POSTGRES_USER" \
     --dbname "$SOURCE_DATABASE" \
     --format=custom \
     --file="/tmp/$BACKUP_ID.dump"
   docker exec "$POSTGRES_CONTAINER" sha256sum "/tmp/$BACKUP_ID.dump"
   ```

5. Capture the full source fingerprint again after the dump. Record backup
   start/end UTC, SHA-256, byte size, source revision, and write-freeze
   duration. Report `quiescence_enforced=true`, `measured_data_loss_rows=0`,
   and `data_loss_window_seconds=0.0` only when the enforced freeze, stable
   source fingerprints, and restored fingerprint equality are all proven.
   Otherwise fail the drill and emit no pass evidence.
6. In all success and failure paths, restore
   `default_transaction_read_only=off`, reconnect, and prove
   `transaction_read_only=off`. Preserve the primary failure if this cleanup
   also fails.

## Restore And Check

1. Create a distinct restore target. Never restore over the source database:

   ```bash
   docker exec "$POSTGRES_CONTAINER" createdb \
     --username "$POSTGRES_USER" "$RESTORE_DATABASE"
   docker exec "$POSTGRES_CONTAINER" pg_restore \
     --username "$POSTGRES_USER" \
     --exit-on-error \
     --dbname "$RESTORE_DATABASE" \
     "/tmp/$BACKUP_ID.dump"
   ```

2. Connect only to the distinct restore database and verify:
   - Alembic revision is `0010_ai_tasks`.
   - `Database.ready()` passes the full packaged schema contract.
   - The release verifier records any package-metadata canonicalization required
     after PostgreSQL reparses dumped expressions; do not hand-edit the schema.
   - Immutable source, import, snapshot, and audit sentinel rows are present.
   - The current permission sentinel is unchanged.
   - Restored counts and full fingerprint match the quiesced source.
   - The source fingerprint remains unchanged.
3. Record elapsed restore time and compare it with the suggested, unapproved
   RTO target.

## Switch Or Abort

Switch only when every check passes, the backup SHA-256 is recorded, the source
is retained, and application owners approve the target. Update the connection
secret atomically, start the prior compatible application artifact, run
readiness checks, then admit writes.

Abort the switch on any backup, restore, revision, `Database.ready()`, sentinel,
permission, count, fingerprint, or elapsed-time check failure. Keep writes
stopped, retain the original source, mark the release blocked, and escalate.
Never attempt an in-place repair as part of this procedure.

## Cleanup And Audit

Cleanup is conditional on whether the switch occurred:

1. After a successful switch, retain the newly active restored database. Never
   drop the active restored database. Retain the old source database for the
   rollback window, and remove the dump according to the retention policy.
2. On abort before switch, retain the original source and evidence. Operators
   may drop the failed restore database after recording its identity and
   failure, then remove the dump according to the retention policy.
3. A disposable drill that never becomes a production switch may drop its
   isolated restore database and remove its dump after verification.

The audit record must contain incident and operator IDs, application artifact,
source and restore database IDs, write-stop and switch/abort UTC times, revision,
backup SHA-256 and size, source/restore fingerprints, sentinel and permission
status, row counts, quiescence proof, measured data-loss rows and window,
write-freeze duration, elapsed duration, cleanup status, retention deadlines,
decision, and approvers.
