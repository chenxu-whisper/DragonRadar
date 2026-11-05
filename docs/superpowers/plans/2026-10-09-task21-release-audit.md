# Task 21 Migration Release Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a repeatable migration release gate and truthful Go/No-Go report for the committed Task 00-20 baseline without merging or publishing the branch.

**Architecture:** Freeze the existing Alembic graph with a packaged hash manifest, compare live PostgreSQL catalog structure with a packaged schema contract, and exercise declared upgrade baselines plus wheel-only migration and backup restoration. A release auditor derives four independent decisions from executed checks, committed Task 20 evidence, Git state, and explicit external attestations.

**Tech Stack:** Python 3.12, Alembic, SQLAlchemy, PostgreSQL 17, pytest, Docker, GitHub Actions, JSON, Markdown

---

## File Map

- Create `src/dragon_radar/migrations/release_manifest.json`: immutable revision graph and SHA-256 baseline.
- Create `src/dragon_radar/storage/schema_contract.py`: normalized PostgreSQL catalog reader and comparator.
- Create `src/dragon_radar/storage/schema_contract.json`: packaged expected head schema.
- Modify `src/dragon_radar/storage/database.py`: use the structural contract in readiness.
- Create `scripts/verify_release_migrations.py`: graph, hash, upgrade, wheel, and restore checks.
- Create `scripts/audit_reset_release.py`: derive the four release decisions and write evidence.
- Create `tests/test_release_migrations.py`: migration and schema-drift tests.
- Create `tests/test_release_audit.py`: release decision and evidence-tampering tests.
- Create `docs/operations/database-backup-restore.md`: restore-only rollback runbook.
- Create `docs/acceptance/evidence/task21/release-manifest.json`: generated committed audit summary.
- Create `docs/acceptance/reset-release.md`: human Go/No-Go report.
- Modify `.github/workflows/ci.yml`: wheel migration and release-audit jobs.

The existing 18 migration files are read-only. Do not edit them.

### Task 1: Freeze And Verify The Migration Graph

**Files:**
- Create: `src/dragon_radar/migrations/release_manifest.json`
- Create: `scripts/verify_release_migrations.py`
- Create: `tests/test_release_migrations.py`

- [ ] **Step 1: Write failing graph and tamper tests**

Add tests that load the real script through `runpy`, verify the current graph,
then mutate a copied migration and manifest:

```python
def test_release_manifest_matches_single_linear_graph(repository_root):
    result = verify_manifest(repository_root)
    assert result["head"] == "0010_ai_tasks"
    assert result["revision_count"] == 18
    assert result["errors"] == []


def test_release_manifest_rejects_rewritten_or_unlisted_revision(tmp_path):
    root = copy_migration_package(tmp_path)
    (root / "versions" / "0001_foundation.py").write_text("# changed\n")
    errors = verify_manifest(root)["errors"]
    assert "SHA-256 mismatch: 0001_foundation.py" in errors


def test_release_manifest_rejects_branch_or_multiple_heads(tmp_path):
    root = copy_migration_package(tmp_path)
    add_revision(root, revision="fork", down_revision="0009d_reg_event_complete_set")
    errors = verify_manifest(root)["errors"]
    assert any("exactly one head" in error for error in errors)
```

- [ ] **Step 2: Run tests and confirm RED**

Run:

```powershell
uv run pytest tests/test_release_migrations.py -q
```

Expected: FAIL because the manifest and verifier do not exist.

- [ ] **Step 3: Implement strict manifest validation**

Implement these public functions in `scripts/verify_release_migrations.py`:

```python
def migration_sha256(path: Path) -> str: ...
def load_release_manifest(path: Path) -> dict[str, object]: ...
def inspect_alembic_graph(root: Path) -> dict[str, object]: ...
def verify_manifest(root: Path) -> dict[str, object]: ...
```

The verifier must derive revision and parent values from Alembic's
`ScriptDirectory`, require one base and one head, require a linear path, compare
the exact sorted revision set, and hash raw file bytes. It must reject unknown
manifest fields and caller-supplied pass/status fields.

Create `release_manifest.json` with:

```json
{
  "schema": "dragon-radar.migration-release-manifest",
  "schema_version": 1,
  "head": "0010_ai_tasks",
  "rollback_mode": "backup_restore_only",
  "supported_from": [
    "0001_foundation",
    "0003_private_review",
    "0004a_topic_attempt_ranges",
    "0006_market_readmodels",
    "0007_role_readmodels",
    "0007c_analysis_jobs",
    "0008_news",
    "0009_regulation",
    "0009d_reg_event_complete_set",
    "0010_ai_tasks"
  ],
  "revisions": []
}
```

Populate `revisions` with exact filename, revision, parent, SHA-256, and byte
count for all 18 current files.

- [ ] **Step 4: Verify GREEN and formatting**

Run:

```powershell
uv run pytest tests/test_release_migrations.py -q
uv run ruff check scripts/verify_release_migrations.py tests/test_release_migrations.py
uv run ruff format --check scripts/verify_release_migrations.py tests/test_release_migrations.py
```

Expected: all pass.

- [ ] **Step 5: Commit only Task 1 files**

```powershell
git add src/dragon_radar/migrations/release_manifest.json scripts/verify_release_migrations.py tests/test_release_migrations.py
git commit -m "feat: freeze reset migration release graph"
```

### Task 2: Detect Structural Schema Drift In Readiness

**Files:**
- Create: `src/dragon_radar/storage/schema_contract.py`
- Create: `src/dragon_radar/storage/schema_contract.json`
- Modify: `src/dragon_radar/storage/database.py`
- Modify: `tests/test_release_migrations.py`

- [ ] **Step 1: Write failing drift tests**

Use a fresh isolated migrated schema for each case:

```python
@pytest.mark.parametrize(
    "ddl",
    [
        "DROP TRIGGER immutable_rows ON snapshots",
        "ALTER TABLE snapshots DROP CONSTRAINT snapshots_source_id_trade_date_revision_key",
        "DROP INDEX snapshots_listing",
        "ALTER TABLE import_batches DROP COLUMN payload_hash",
    ],
)
def test_readiness_rejects_required_schema_drift(database, ddl):
    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(text(ddl))
    assert not database.ready()
```

Use catalog lookup in the test to assert each selected object name exists before
dropping it; adjust only to the actual Alembic-generated identifier.

- [ ] **Step 2: Run the four tests and confirm RED**

Run:

```powershell
uv run pytest tests/test_release_migrations.py -q -k "schema_drift"
```

Expected: each mutation leaves `Database.ready()` true.

- [ ] **Step 3: Implement normalized catalog capture**

Implement:

```python
def capture_schema_contract(connection: Connection) -> dict[str, object]:
    return {
        "tables": capture_tables_and_columns(connection),
        "constraints": capture_constraints(connection),
        "indexes": capture_indexes(connection),
        "triggers": capture_triggers(connection),
    }


def compare_schema_contract(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> list[str]: ...
```

Catalog records must use stable structural fields:

- columns: table, name, PostgreSQL type, nullable, identity, generated;
- constraints: table, name, kind, ordered columns, referenced table/columns;
- indexes: table, name, unique, valid, ordered key expressions;
- triggers: table, name, enabled, timing/events, function name.

Exclude PostgreSQL internal objects and the `alembic_version` table. Sort all
lists before JSON serialization.

- [ ] **Step 4: Generate and package the head contract**

Add a `--write-schema-contract` mode to `verify_release_migrations.py`. It must
refuse to write unless the database is at `0010_ai_tasks` and the worktree has no
tracked migration changes.

Run against a disposable schema:

```powershell
uv run python scripts/verify_release_migrations.py --write-schema-contract src/dragon_radar/storage/schema_contract.json
```

Review the JSON for schema-qualified names, credentials, owners, and unstable
OIDs; none are allowed.

- [ ] **Step 5: Bind readiness to the packaged contract**

Replace the table-only loop in `Database.ready()` with:

```python
expected = load_packaged_schema_contract()
actual = capture_schema_contract(connection)
return not compare_schema_contract(expected, actual)
```

Keep revision equality as the first check. Any SQL/catalog error returns false.

- [ ] **Step 6: Verify GREEN and regression scope**

Run:

```powershell
uv run pytest tests/test_release_migrations.py tests/test_storage.py tests/test_topic_migrations.py -q
uv run pytest tests/test_reset_publication.py tests/test_reset_role_publication.py tests/test_analysis_jobs.py tests/test_reset_news.py tests/test_reset_regulation.py tests/test_reset_ai_tasks.py -q -k "migration or readiness"
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit only Task 2 files**

```powershell
git add src/dragon_radar/storage/schema_contract.py src/dragon_radar/storage/schema_contract.json src/dragon_radar/storage/database.py tests/test_release_migrations.py scripts/verify_release_migrations.py
git commit -m "feat: verify reset schema readiness"
```

### Task 3: Exercise Supported Upgrades And The Installed Wheel

**Files:**
- Modify: `scripts/verify_release_migrations.py`
- Modify: `tests/test_release_migrations.py`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write failing upgrade-matrix tests**

Parameterize the exact `supported_from` list. For each baseline:

```python
@pytest.mark.parametrize("revision", SUPPORTED_FROM)
def test_supported_revision_upgrades_with_sentinel(database_url, revision):
    result = exercise_upgrade(database_url, revision)
    assert result["from_revision"] == revision
    assert result["to_revision"] == "0010_ai_tasks"
    assert result["sentinel_preserved"] is True
    assert result["ready"] is True
```

Sentinels are revision-aware:

- `0001`: source grant, import batch, snapshot, audit event;
- `0003`: owner principal and private audit row;
- `0004a`: topic attempt and batch;
- `0006`: security and market import attempt;
- `0007`: role run root;
- `0007c`: analysis job;
- `0008`: news article/revision;
- `0009`: regulation rule;
- `0009d`: regulation event provenance;
- `0010`: AI task.

Each fixture inserts only columns present at that revision and verifies stable
IDs and payload hashes after upgrading.

- [ ] **Step 2: Run the matrix and confirm RED**

Run:

```powershell
uv run pytest tests/test_release_migrations.py -q -k "supported_revision"
```

Expected: FAIL because `exercise_upgrade` and sentinel fixtures do not exist.

- [ ] **Step 3: Implement isolated upgrade execution**

Add:

```python
def exercise_upgrade(database_url: str, revision: str) -> dict[str, object]: ...
def exercise_supported_upgrades(database_url: str) -> list[dict[str, object]]: ...
```

Each case creates a unique schema, runs Alembic to the baseline, inserts the
sentinel, upgrades to head, verifies the sentinel and schema contract, and drops
the schema in `finally`.

- [ ] **Step 4: Add wheel-only smoke mode**

Add `--wheel PATH` mode. It must:

1. create `.temp/task21-wheel-venv`;
2. install the wheel and dependencies with `uv pip install --python`;
3. run a helper from `.temp/task21-wheel-run`, outside the repository package;
4. print the imported `dragon_radar.__file__` and reject paths under `src/`;
5. execute fresh and supported upgrade checks using packaged resources.

The test builds a wheel and asserts all 18 migration files, the release
manifest, and schema contract are present in the archive.

- [ ] **Step 5: Add the CI wheel migration gate**

After `uv build --out-dir .temp/dist` in `verify`, add:

```yaml
      - name: Verify installed wheel migrations
        run: >-
          uv run python scripts/verify_release_migrations.py
          --database-url "$DRAGON_TEST_DATABASE_URL"
          --wheel .temp/dist/dragon_radar-0.1.0-py3-none-any.whl
          --output .temp/acceptance/task21-migrations.json
```

Do not hard-code the wheel filename in the final script; resolve exactly one
`.whl` from `.temp/dist` and fail on zero or multiple matches.

- [ ] **Step 6: Verify and commit**

Run:

```powershell
uv build --out-dir .temp/task21-dist
uv run python scripts/verify_release_migrations.py --database-url $env:DRAGON_TEST_DATABASE_URL --wheel .temp/task21-dist/*.whl --output .temp/acceptance/task21-migrations.json
uv run pytest tests/test_release_migrations.py -q
```

Commit:

```powershell
git add scripts/verify_release_migrations.py tests/test_release_migrations.py .github/workflows/ci.yml
git commit -m "test: exercise reset release migrations"
```

### Task 4: Enforce Restore-Only Rollback And Drill Backup Recovery

**Files:**
- Modify: `scripts/verify_release_migrations.py`
- Modify: `tests/test_release_migrations.py`
- Create: `docs/operations/database-backup-restore.md`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write failing rollback-policy and restore tests**

```python
def test_release_policy_forbids_alembic_downgrade():
    policy = load_release_manifest()
    assert policy["rollback_mode"] == "backup_restore_only"
    with pytest.raises(ValueError, match="backup_restore_only"):
        validate_rollback_request("alembic_downgrade")


def test_backup_restore_drill_preserves_release_sentinel(postgres_container):
    result = exercise_backup_restore(postgres_container)
    assert result["source_unchanged"] is True
    assert result["restored_ready"] is True
    assert result["sentinel_preserved"] is True
    assert result["elapsed_seconds"] > 0
```

Include a test proving no release script imports or calls
`alembic.command.downgrade`.

- [ ] **Step 2: Confirm RED**

Run:

```powershell
uv run pytest tests/test_release_migrations.py -q -k "rollback or backup_restore"
```

- [ ] **Step 3: Implement the Docker-backed drill**

Add `--restore-container NAME` mode. The verifier runs argument arrays without
`shell=True`:

```text
docker exec NAME pg_dump --format=custom --file=/tmp/task21.dump ...
docker exec NAME createdb ... dragon_restore
docker exec NAME pg_restore --exit-on-error --dbname=dragon_restore /tmp/task21.dump
```

Use generated test-only credentials, never include them in the JSON output, and
delete the dump and restore database in `finally`. Verify the restored revision,
schema contract, sentinel rows, current permissions, and source database hash.

- [ ] **Step 4: Write the operator runbook**

Document exact preconditions, stop-write step, custom-format backup command,
separate restore target, readiness/sentinel checks, switch criteria, abort
criteria, cleanup, and audit fields. State that RPO <= 1 hour and RTO <= 4 hours
are suggested targets pending approval.

- [ ] **Step 5: Add a CI restore job**

Create a `task21_migration_release` job with a named pinned PostgreSQL 17
container, run the wheel gate and restore drill, use `if: always()` cleanup, and
upload `.temp/acceptance/task21-migrations.json`.

- [ ] **Step 6: Verify and commit**

Run the focused pytest and one real Docker restore drill. Commit only Task 4
files:

```powershell
git add scripts/verify_release_migrations.py tests/test_release_migrations.py docs/operations/database-backup-restore.md .github/workflows/ci.yml
git commit -m "test: verify reset backup restoration"
```

### Task 5: Derive The Release Manifest And Go/No-Go Report

**Files:**
- Create: `scripts/audit_reset_release.py`
- Create: `tests/test_release_audit.py`
- Create: `docs/acceptance/evidence/task21/release-manifest.json`
- Create: `docs/acceptance/reset-release.md`

- [ ] **Step 1: Write failing decision tests**

```python
def test_external_blocker_cannot_be_overridden_by_automated_pass():
    report = derive_decisions(valid_inputs(external={"licensed_input": "BLOCKED"}))
    assert report["migration_readiness"] == "GO"
    assert report["production_release"] == "NO_GO"
    assert report["overall_status"] == "NO_GO"


def test_dirty_or_unbased_branch_blocks_branch_cut():
    dirty = derive_decisions(valid_inputs(tracked_dirty=True))
    stale = derive_decisions(valid_inputs(upstream_verified=False))
    assert dirty["branch_cut"] == "NO_GO"
    assert stale["branch_cut"] == "NO_GO"


def test_caller_supplied_status_fields_are_rejected():
    payload = valid_inputs()
    payload["overall_status"] = "GO"
    with pytest.raises(ValueError, match="unexpected field"):
        derive_decisions(payload)
```

Also test missing Task 20 artifact, hash mismatch, failed migration check,
missing security/SBOM/container scan, and docs-only evidence commit relation.

- [ ] **Step 2: Confirm RED**

Run:

```powershell
uv run pytest tests/test_release_audit.py -q
```

- [ ] **Step 3: Implement strict input collection and derived decisions**

Implement:

```python
def collect_git_state(root: Path) -> dict[str, object]: ...
def collect_task20_evidence(root: Path) -> dict[str, object]: ...
def collect_migration_evidence(path: Path) -> dict[str, object]: ...
def derive_decisions(inputs: Mapping[str, object]) -> dict[str, object]: ...
def write_release_manifest(path: Path, report: Mapping[str, object]) -> None: ...
```

Run Git and verifier commands directly with argv arrays. Record HEAD, base,
upstream, merge-base, ahead/behind, tracked dirty paths, and evidence hashes.
Never include credentials, absolute private paths, full environment values, or
caller-supplied status.

Use exact external gate IDs:

```python
EXTERNAL_GATES = (
    "licensed_real_input",
    "approved_official_rules",
    "real_model_evaluation",
    "public_sharing_permit",
    "twenty_trading_day_shadow_run",
)
```

Missing supply-chain scans are separate internal blockers:
`secret_scan`, `python_dependency_scan`, `node_dependency_scan`, `sbom`,
`container_scan`.

- [ ] **Step 4: Generate the committed manifest from a clean detached worktree**

The generated JSON must include:

- audited implementation commit and report commit relation;
- migration graph/hash/schema/upgrade/wheel/restore outcomes;
- Task 00-20 evidence references and hashes;
- branch state and remote freshness;
- internal and external blockers;
- the four decisions and derived overall status.

Current truthful expectation is `NO_GO` overall. Do not turn missing
attestations or scans into pass values.

- [ ] **Step 5: Write the human report**

`docs/acceptance/reset-release.md` must present findings first, then:

- migration chain and supported baselines;
- Task 00-20 evidence inventory;
- performance/recovery/security limitations;
- four decision table;
- exact blockers, owner/evidence needed, and recheck command;
- explicit statement that no merge, tag, version bump, or deployment occurred.

- [ ] **Step 6: Verify and commit**

Run:

```powershell
uv run pytest tests/test_release_audit.py -q
uv run python scripts/audit_reset_release.py --check docs/acceptance/evidence/task21/release-manifest.json
```

Commit code/tests first, then generated evidence/docs:

```powershell
git add scripts/audit_reset_release.py tests/test_release_audit.py
git commit -m "feat: derive reset release decisions"
git add docs/acceptance/evidence/task21/release-manifest.json docs/acceptance/reset-release.md
git commit -m "docs: record Task21 release audit"
```

### Task 6: Final Verification And Independent Review

**Files:**
- Modify only files required by confirmed review findings.

- [ ] **Step 1: Run focused release verification in a clean worktree**

```powershell
uv run pytest tests/test_release_migrations.py tests/test_release_audit.py -q
uv run ruff check scripts/verify_release_migrations.py scripts/audit_reset_release.py tests/test_release_migrations.py tests/test_release_audit.py src/dragon_radar/storage/schema_contract.py
uv run ruff format --check scripts/verify_release_migrations.py scripts/audit_reset_release.py tests/test_release_migrations.py tests/test_release_audit.py src/dragon_radar/storage/schema_contract.py
uv run python -m compileall -q src tests scripts
```

- [ ] **Step 2: Run the migration, wheel, and restore gates**

Execute the full Task 21 migration verifier against disposable PostgreSQL 17
and retain the JSON under `.temp/acceptance/`.

- [ ] **Step 3: Re-run Task 20 integrity and affected full gates**

```powershell
uv run python scripts/verify_task20_bundle.py
uv run pytest -q -W error
```

Run the frontend suites only if Task 21 changes `.github/workflows/ci.yml` or
shared build configuration in a way that affects their command graph.

- [ ] **Step 4: Perform spec compliance review**

Review the Task 21 commit range against
`docs/superpowers/specs/2026-10-09-task21-release-audit-design.md`. Fix every
missing or extra requirement and re-run affected checks.

- [ ] **Step 5: Perform code quality review**

Review security, subprocess argument safety, catalog stability, cleanup on
failure, evidence trust boundaries, secret redaction, and CI teardown. Fix every
Critical or Important issue and re-run affected checks.

- [ ] **Step 6: Refresh evidence and close without merging**

Generate final evidence from the reviewed clean implementation commit, create a
docs/evidence-only commit if needed, and report:

- implementation and evidence SHAs;
- migration and test results;
- measured restore duration;
- all four decisions;
- unresolved blockers.

Do not merge, tag, bump the version, publish an image, or deploy.
