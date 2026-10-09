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
2. Record the UTC write-stop time and verify active write transactions have
   drained.
3. Create the custom-format backup without printing credentials:

   ```bash
   docker exec "$POSTGRES_CONTAINER" pg_dump \
     --username "$POSTGRES_USER" \
     --dbname "$SOURCE_DATABASE" \
     --format=custom \
     --file="/tmp/$BACKUP_ID.dump"
   docker exec "$POSTGRES_CONTAINER" sha256sum "/tmp/$BACKUP_ID.dump"
   ```

4. Record backup start/end UTC, SHA-256, byte size, source revision, and the
   measured data-loss window. A fully quiesced source with an unchanged
   fingerprint has a measured data-loss window of zero.

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

After a successful switch or explicit abort, preserve evidence, then clean up:

```bash
docker exec "$POSTGRES_CONTAINER" dropdb \
  --username "$POSTGRES_USER" --if-exists "$RESTORE_DATABASE"
docker exec "$POSTGRES_CONTAINER" rm -f "/tmp/$BACKUP_ID.dump"
```

The audit record must contain incident and operator IDs, application artifact,
source and restore database IDs, write-stop and switch/abort UTC times, revision,
backup SHA-256 and size, source/restore fingerprints, sentinel and permission
status, row counts, measured data-loss window, elapsed duration, cleanup status,
decision, and approvers.
