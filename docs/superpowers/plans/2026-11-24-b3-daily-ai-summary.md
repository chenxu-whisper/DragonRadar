# B3 Daily AI And B4a Promoted Summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish verified owner-approved daily AI sidecars and promote exact article summaries for explicit later news materialization without exposing private tasks.

**Architecture:** Keep public reads in a repository with no AI task repository dependency. An owner-only promotion service authenticates in its transaction, checks immutable evidence and exact review/revision hashes, independently verifies output, and writes public artifacts plus separate private links. Current and frozen source/output permissions and append-only withdrawals guard every read and replay; daily AI binds the existing PUB and dependency sequence, never a sixth domain.

**Tech Stack:** Python 3.12+, Pydantic 2, SQLAlchemy Core/PostgreSQL, Alembic, FastAPI, pytest.

---

## Authority And Ownership

The unified design is approved. Data-quality-research-design sections 3 and 12 override summarize assumptions: input is exactly `{"article": archived_article}`, never `notes_context`. Only authenticated owner POST can promote. CLI selection references already promoted summary IDs/revisions and exact article revisions.

ROOT is `E:/ByteDance/Projects/Code/.temp/DragonRadar-AllRequirements-20261124`.
No commits, staging, shared app/public_app/gateway/schema/release/env/dependency mappings, domain quality/models, market readmodels, analysis, sentiment, or research edits.
Parent owns common wiring and retention dispatch. No common clock fields are needed: artifact timestamps use the injected UTC clock, existing review creation time, and independent verification completion time.

## File Map

- Create `src/dragon_radar/public_daily_ai/models.py`: closed commands, entitlement, public payload and archive contracts, canonical digests.
- Create `src/dragon_radar/public_daily_ai/tables.py`: output registry/revocations, archives, private links, promotion/revocation ledgers, independent verification records and promoted summary registry.
- Create `src/dragon_radar/public_daily_ai/entitlements.py`: operator registration/revocation and current/frozen entitlement authorization.
- Create `src/dragon_radar/public_daily_ai/repository.py`: public archive selection/discovery, permission recheck, immutable registry reads, dependency binding.
- Create `src/dragon_radar/public_daily_ai/service.py`: public daily envelope and watermarked cursor validation.
- Create `src/dragon_radar/ai/publication.py`: owner-only daily publication, summary promotion, exact provider edit ancestry and owner withdrawals.
- Create `src/dragon_radar/http/routes/daily_ai.py`: two strict public GETs, error mapping, no-store.
- Create `src/dragon_radar/http/routes/ai_publication.py`: four owner POSTs using existing OwnerWrite gate.
- Create `src/dragon_radar/public_daily_ai/cli.py`: explicit entitlement register/revoke module CLI; no owner/task access.
- Create `src/dragon_radar/news/summary_publication.py`: strict artifact, selection and v3 news materializer.
- Modify `src/dragon_radar/http/routes/news.py`: v2/v3 validation and list/detail summary projection via current registry authorization.
- Create `src/dragon_radar/migrations/versions/0015_daily_ai.py`: additive predecessor 0014_research, append-only guards, reverse dependency index, retention-only payload erasure.
- Create `tests/test_b3_daily_ai_contracts.py`, `tests/test_b3_daily_ai_repository.py`, `tests/test_b3_daily_ai_routes.py`, `tests/test_b3_summary_publication.py`: dedicated tests only.
- Create `docs/acceptance/b3-daily-ai-summary-2026-11-24.md`: exact integration hooks, evidence, remaining production gates.

## Commands And Logs

All Python commands run with `$env:PYTHONPATH="$PWD/src"` and `.venv/Scripts/python.exe`.
Raw outputs use `Tee-Object .temp/b3-daily-ai/<phase>.log`.
DB tests use only `DRAGON_TEST_DATABASE_URL` whose database equals `dragon_test`, with the existing random disposable-schema fixture. No dailyDB or service operations.

```powershell
$env:PYTHONPATH="$PWD/src"
& .venv/Scripts/python.exe -m pytest tests/test_b3_daily_ai_contracts.py -q 2>&1 | Tee-Object .temp/b3-daily-ai/contracts-red.log
```

## Task 1: Closed Contracts And Eligibility

- [x] Read approved specs, existing task manifest/review/provider verification, source permissions, publication dependencies and test fixtures.
- [x] Run bits-unit-test-gen prepare script; record unsupported Windows utree bootstrap without hiding it.
- [x] Load Python knowledge, determine explicit module scope, and output targets/defect analysis before tests.
- [x] Write contract tests for exact command keys, bool/negative revision rejection, output entitlement public audience and expiry, private content exclusion, independent verification, and article-only input.

```python
def test_summary_input_is_article_only():
    candidate = eligible_candidate(task_type="summarize")
    assert validate_candidate(**candidate).provenance == "ai"
    candidate["task"]["input_manifest"]["input"]["notes_context"] = ""
    with pytest.raises(Conflict, match="private_input"):
        validate_candidate(**candidate)
```

- [x] Run contract tests RED. Expected assertion failures for missing target modules/behavior; fix fixture/import infrastructure before accepting RED.
- [x] Implement closed Pydantic models and pure owner eligibility/projection.

```python
def digest(value):
    return hashlib.sha256(canonical_object(value).encode("ascii")).hexdigest()
```

Eligibility explicitly compares succeeded/passed/accepted, selected revision, exact accept audit task/owner/resulting revision, canonical hash, immutable manifest against authorized original PUB, and provider ancestry through edit audits. Re-run `verify_output`; unknown output fields fail closed. Public citations project only locator, original fragment, source identity, evidence ID and allowed public PUB/news/security targets.

- [x] Run contract tests GREEN, including every independent rejection condition. Retain RED and GREEN logs.

## Task 2: Registries, Migration And Owner Transaction

- [ ] Write disposable-schema tests for tables/head predecessor, append-only guards, entitlement registration/revocation, publish versions/idempotency/owner isolation, exact review lookup where accept draft_id is null, and source dependency sequence.

```python
def test_replay_rechecks_output_rights(setup):
    receipt = setup.publish(key="same")
    setup.entitlements.revoke("output-public", key="withdraw")
    with pytest.raises(Forbidden):
        setup.publish(key="same")
    assert setup.archived_hash(receipt.archive_id) == receipt.content_hash
```

- [x] Run repository tests RED using `dragon_test` only.
- [x] Add SQLAlchemy mappings and migration. Immutable ledgers reject UPDATE/DELETE/TRUNCATE. Payload erasure must require a bound storage withdrawal and completed cutoff binding; ordinary revision mutation remains forbidden.
- [x] Implement entitlement locking with a shared source-compatible lock order, explicit audience/provider/model/version matching and append-only output revocations.
- [x] Implement owner service using existing task/owner repositories only on private path. Authenticate current token+CSRF in transaction, lock PUB/version, source/output rights, task and exact selected draft/review, independently verify, write immutable archives/links/proofs/dependencies. Same key/different body conflicts. Same key/same body rechecks current authorization without rebasing archived bytes.
- [x] Implement owner revoke ledgers with strict private linkage and expected version; no mutation of task/PUB/artifact.
- [ ] Run repository tests GREEN and preserve SQL/pytest logs.

## Task 3: Public Reads And Watermarked Discovery

- [ ] Write tests for null/unavailable, explicit missing ID 404, explicit revoked ID 403, no fallback for explicit ID, date/PUB mismatch, corrupt archive 503, current rights expiry/revocation, and bounded watermarked pagination.

```python
def test_discovery_watermark_excludes_later_publish(setup):
    setup.publish(key="v1")
    setup.publish(key="v2", expected_archive_version=1)
    page = setup.public.discover(setup.pub_id, setup.date, limit=1)
    setup.publish(key="v3", expected_archive_version=2)
    tail = setup.public.discover(
        setup.pub_id, setup.date, limit=1, cursor=page["next_cursor"]
    )
    assert [item["archive_version"] for item in tail["items"]] == [1]
```

- [x] Run public repository/service tests RED.
- [x] Implement public read repository with no task repository imports. Recheck source current/frozen storage/display/AI/sharing, output current/frozen permission, PUB and sidecar revocations on every operation. No cached permission conclusions.
- [x] Implement version-descending/ID-ascending discovery cursor binding PUB/date/watermark/last key, bounds 1..100, and daily PUB envelope preserving market as_of plus distinct archive publication time.
- [x] Add strict standalone GET router and isolated OwnerWrite POST installer; no shared app wiring.
- [x] Run route tests with real OwnerGate session/CSRF and public application without task services. Reject duplicate/unknown/missing query/body keys before storage.
- [ ] Run GREEN, retaining logs.

## Task 4: Promoted Summary Materialization And News Reads

- [ ] Write tests for article-only summarize promotion, exact article hash/revision and source binding, long astral Unicode first-100 preview, fresh verification/acceptance after human edit, summary withdrawal while licensed metadata remains, and immutable old PUB.

```python
def test_preview_counts_code_points():
    artifact = valid_artifact(text="\U0001f600" * 101)
    result = preview(artifact)
    assert result["text"] == "\U0001f600" * 100
    assert result["truncated"] is True
```

- [x] Run summary tests RED.
- [x] Implement strict `NewsSummaryArtifact` v1, UUID/revision/digest/version/timestamp checks, registry projection and current plus frozen authorization.
- [x] Add a v3 materializer delegating existing NewsService materialization then attaching only exact selected promoted artifacts. Canonical selection identity participates in the parent's publication command hash. No private task read or promotion in this path.
- [x] Add list/detail projection for legacy v2 unavailable and validated v3 artifacts. Corrupt present artifact raises DATA_UNAVAILABLE; denial clears derived preview independently from licensed article metadata.
- [x] Expose summary eligibility/source dependency tuples for parent publication binding and retention handlers.
- [ ] Run summary and existing news behavior tests GREEN; preserve original v2 rows.

## Task 5: CLI, Final Verification And Handoff

- [x] Write and observe RED tests for strict entitlement document CLI and canonical summary selection with no private task fields.
- [x] Implement module CLI using argparse and structured JSON; no implicit DB work during imports or help.
- [ ] Run owned tests, focused existing AI verification/news regressions, Ruff, compilation and migration predecessor/trigger checks. Never claim production release ready before parent shared head/schema wiring.

```powershell
& .venv/Scripts/python.exe -m pytest tests/test_b3_daily_ai_contracts.py tests/test_b3_daily_ai_repository.py tests/test_b3_daily_ai_routes.py tests/test_b3_summary_publication.py -q 2>&1 | Tee-Object .temp/b3-daily-ai/final-tests.log
& .venv/Scripts/python.exe -m ruff check src/dragon_radar/public_daily_ai src/dragon_radar/ai/publication.py src/dragon_radar/http/routes/daily_ai.py src/dragon_radar/http/routes/ai_publication.py src/dragon_radar/news/summary_publication.py tests/test_b3*.py
```

- [x] Execute bits-unit-test-gen `utree flush`, preserve unavailable-tool evidence if unsupported, and write report with scope/defect analysis/cases/results.
- [x] Write parent hooks: service state injection, GET/private POST installers, public gateway allowlist, source_dependencies daily_ai dispatch and cutoff/seal binding, v3 news materializer/selection identity, summary source/output dependencies, migration/schema/release head coordination.
- [ ] Confirm only owned files changed, no staging/commits, no common clock edits. Distinguish synthetic engineering verification from genuine model/evaluation/sharing enabling inputs.

## Plan Review

Coverage maps AI1 qualification/privacy/entitlements/versioning/replay/public discovery/revocation to Tasks 1-3; B4a exact article-only promotion/v3/preview/withdrawal to Task 4; common wiring and real production gates to Task 5.
No sixth publication domain, no arbitrary-task CLI, no inferred sharing rights, no retroactive news enrichment.
Execution is inline as requested; approval is already supplied and no stale design heading is an implementation gate.

## Verification Checkpoint

Owned implementation steps above are complete; unchecked full-integration
coverage/GREEN steps are intentionally open. Evidence:

- 35 pure Python tests passed (`pure-final-second.log`).
- Fresh/prior-head migration, owner/CSRF, eligibility and sealed erasure:
  5 passed (`migration-owner-retention-final.log`).
- Existing AI verification suite: 40 passed
  (`ai-verification-with-secret-final.log`).
- Existing news focused regression: 6 passed, 1 old exact-key contract failed
  because the approved list now includes ai_preview/revision_id/received_at.
- Owned UI helpers: 4 passed. No browser or five-build acceptance claim.
- Owned Ruff clean and compileall exit 0.
- Full owned/parent retention: 5 passed, 5 failures at parent dependency
  filtering and retention dispatch (`full-owned-parent-retention-final.log`).
- Shared tsc currently fails only in parent B8 fixture. No shared edits made.
- Mandatory utree flush attempted; executable unavailable after unsupported
  cygwin bootstrap. Raw failure retained (`utree-flush.log`).
