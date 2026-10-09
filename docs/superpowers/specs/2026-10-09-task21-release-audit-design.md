# Task 21 Migration Release Audit Design

## Goal

Audit the Task 00-20 reset baseline without merging or publishing it. Produce a
repeatable migration release gate, a machine-readable release manifest, and a
human Go/No-Go report that distinguish engineering readiness from production
eligibility.

This Task 21 definition supersedes the older plan entry that used the same task
number for realtime features. Realtime ingestion and migration `0011_realtime`
are outside this task.

## Boundaries

- Do not merge `feature/prd-reset`.
- Do not add a database revision.
- Do not rewrite any existing migration.
- Do not stage, normalize, or revert unrelated worktree changes.
- Run release evidence from a tracked-clean detached worktree.
- Do not treat synthetic tests as licensed production input, approved rules,
  real-model evaluation, public-sharing approval, or a 20-day shadow run.

## Decision Model

The report has four independent decisions:

1. `migration_readiness`: whether the current artifact can create a fresh
   database, upgrade every declared supported revision, detect critical schema
   drift, and restore a backup.
2. `task00_20_engineering`: whether committed Task 00-20 evidence and current
   release checks pass for the audited commit.
3. `branch_cut`: whether the branch has a verified base/upstream and a clean
   candidate source state suitable for tagging or integration.
4. `production_release`: whether all external production attestations and
   deployment checks exist.

`overall_status` is `GO` only when all four decisions are `GO`. A migration or
engineering `GO` never overrides a branch or production `NO_GO`.

## Migration Contract

### Immutable chain

The current chain is one base and one head:

`0001 -> 0002 -> 0003 -> 0004 -> 0004a -> 0005 -> 0006 -> 0007 -> 0007a -> 0007b -> 0007c -> 0008 -> 0009 -> 0009a -> 0009b -> 0009c -> 0009d -> 0010`

`src/dragon_radar/migrations/release_manifest.json` freezes, for every revision,
the filename, revision, parent revision, and SHA-256. A verifier rejects missing,
duplicate, reordered, changed, branched, or unlisted revisions. This is the
bootstrap release baseline; future changes require an additive revision and an
explicit manifest append.

### Supported upgrade baselines

The release policy explicitly lists milestone revisions supported for upgrade:

- `0001_foundation`
- `0003_private_review`
- `0004a_topic_attempt_ranges`
- `0006_market_readmodels`
- `0007_role_readmodels`
- `0007c_analysis_jobs`
- `0008_news`
- `0009_regulation`
- `0009d_reg_event_complete_set`
- `0010_ai_tasks`

Each baseline is created by Alembic, receives revision-appropriate sentinel
data, upgrades to head, and proves that the sentinel remains readable. The
matrix does not infer support from Git history or an untagged development
database.

### Schema readiness

`Database.ready()` compares PostgreSQL catalog state with a packaged schema
contract. The contract covers:

- required tables and columns;
- column type and nullability;
- primary, unique, foreign-key, and check constraints;
- required indexes and uniqueness;
- non-internal triggers, enabled state, event/timing, and trigger function.

Tests remove one required column, constraint, index, and immutability trigger in
isolated schemas and require readiness to fail. The contract uses structural
catalog fields rather than schema-qualified DDL strings so disposable schema
names do not alter the result.

### Rollback policy

Production rollback does not use `alembic downgrade`. Existing downgrade
functions are historical development artifacts and remain byte-immutable.
Release tooling rejects a rollback plan that selects Alembic downgrade.

The supported rollback is:

1. stop writes and record the current application, schema, and backup IDs;
2. create a PostgreSQL custom-format backup;
3. restore it into a separate database;
4. run schema readiness and sentinel checks against the restored database;
5. switch only after verification, retaining the original database.

The drill records elapsed time and measured data-loss window. It reports these
against the suggested RPO of one hour and RTO of four hours without claiming an
approved production target.

## Artifact Verification

CI builds the wheel, installs it into an isolated virtual environment, changes
the working directory outside the source tree, and runs fresh and supported-old
schema upgrades using the installed package. This proves that migration files,
the release manifest, and the schema contract are packaged.

The release audit verifies the committed Task 20 evidence bundle but labels it
as committed execution provenance, not cryptographic attestation. It also
records dependency/SBOM/container/security scan availability. Missing scanners
remain explicit release blockers; they are not silently treated as passed.

## Outputs

- `src/dragon_radar/migrations/release_manifest.json`
- `src/dragon_radar/storage/schema_contract.json`
- `scripts/verify_release_migrations.py`
- `scripts/audit_reset_release.py`
- `tests/test_release_migrations.py`
- `tests/test_release_audit.py`
- `docs/operations/database-backup-restore.md`
- `docs/acceptance/evidence/task21/release-manifest.json`
- `docs/acceptance/reset-release.md`

The machine manifest contains the audited commit, source-state observations,
migration checks, Task 00-20 evidence status, scan status, external gate status,
and the four decisions. Caller-supplied statuses are not accepted; statuses are
derived from captured checks and declared attestations.

## Error Handling

- Missing PostgreSQL, Docker, Git metadata, evidence, or packaged files produces
  `BLOCKED`, never `GO`.
- A dirty tracked worktree blocks `branch_cut` but does not invalidate a clean
  detached audit of the committed implementation.
- An unknown or rewritten migration fails the migration gate.
- A failed backup restore leaves the source database untouched and blocks
  release.
- Unknown external attestations remain `BLOCKED`; an absent value is never
  interpreted as approval.

## Verification

Implementation follows RED/GREEN TDD. Focused checks cover manifest tampering,
multiple heads, schema drift, every supported upgrade baseline, wheel-only
migration, restore verification, dirty branch state, stale or absent upstream,
Task 20 evidence integrity, and derived Go/No-Go decisions.

Final evidence is generated from a clean detached worktree. Independent spec
and quality reviews run before Task 21 is marked complete.
