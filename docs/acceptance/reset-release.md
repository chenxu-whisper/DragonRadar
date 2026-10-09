# Reset Release Audit

Audited implementation:
`c8f1848d0e6d6844232d10c5995a6c51666eeea2`.
Machine evidence:
[release-manifest.json](evidence/task21/release-manifest.json).

## Findings

1. **Overall release is NO_GO.** Engineering and migration evidence cannot
   override the independent branch-cut and production-release gates.
2. **Branch cut is NO_GO.** `feature/prd-reset` has no configured upstream,
   remote freshness is therefore absent, and 27 tracked paths were dirty when
   the audit captured Git state. The audited commit is 307 commits ahead and
   zero behind the local `origin/main` base, but that local relation is not
   remote proof.
3. **Production release is NO_GO.** All five external gates and all five
   internal supply-chain checks are `BLOCKED`. This task provides no caller
   mechanism for converting them to trusted evidence.
4. **Recovery passed with an explicit limitation.** The restored database was
   not ready before Task4's declared schema canonicalization; 50 schema objects
   were canonicalized, after which strict readiness passed. The measured RPO
   and RTO targets remain suggested and unapproved.
5. **Task20 evidence is provenance, not cryptographic attestation.** Its full
   execution records 21 required gates, zero failures, and zero skips. Its
   performance fixture is synthetic and makes no cold-cache or real-input
   production claim.

## Decisions

| Decision | Result | Basis |
| --- | --- | --- |
| `migration_readiness` | **GO** | Frozen graph/hash, wheel-only fresh and upgrade matrix, schema readiness, and restore drill passed |
| `task00_20_engineering` | **GO** | Strict committed Task20 bundle and Task00-20 tracked evidence inventory validated |
| `branch_cut` | **NO_GO** | No upstream or remote freshness proof, and 27 tracked dirty paths |
| `production_release` | **NO_GO** | Five external attestations and five supply-chain checks are absent |
| Overall | **NO_GO** | Overall is GO only when all four independent decisions are GO |

`NO_GO` is the release decision, not a verifier failure. `--check` succeeds
when this committed report remains internally valid and truthful.

## Migration Chain

The frozen single-head chain contains 18 revisions:

`0001 -> 0002 -> 0003 -> 0004 -> 0004a -> 0005 -> 0006 -> 0007 -> 0007a -> 0007b -> 0007c -> 0008 -> 0009 -> 0009a -> 0009b -> 0009c -> 0009d -> 0010`

- Head: `0010_ai_tasks`
- Release-manifest SHA-256:
  `03a3e71c4b7460519290efb32bffe5fd533dedc846e976b7026827196059ee10`
- Schema-contract SHA-256:
  `3ffdfdde83ae26699c96a2c56e2445215dcc86ce7d34ca73f4099043343e59d7`
- Wheel SHA-256:
  `53c25c4ff31d576eed3cf73bedb400ec10c8244322c7179386e27a7fcc2e6d65`

## Upgrade Matrix

The installed wheel ran outside the source tree. Fresh migration reached
`0010_ai_tasks` with strict readiness. Every supported baseline upgraded to
head with its sentinel preserved and strict readiness true.

| Baseline | Sentinel |
| --- | --- |
| `0001_foundation` | `foundation_archive` |
| `0003_private_review` | `private_audit` |
| `0004a_topic_attempt_ranges` | `topic_attempt_batch` |
| `0006_market_readmodels` | `security_market_attempt` |
| `0007_role_readmodels` | `role_run_root` |
| `0007c_analysis_jobs` | `analysis_job` |
| `0008_news` | `news_article_revision` |
| `0009_regulation` | `legacy_regulation_graph` |
| `0009d_reg_event_complete_set` | `regulation_event_provenance` |
| `0010_ai_tasks` | `ai_task` |

The `0009_regulation` sentinel's complete before/after field set is identical
in the machine manifest.

## Restore Evidence

- Custom-format backup SHA-256:
  `8d47abe1663a218cbaa8c81293aae3e1f1d74c3c36d383ff36ac66190804f67b`
- Source and restored fingerprint:
  `5b6c5bc877b0791d7ac84ac8852f0a9174605f654df9b7a418bff1d06bb5d896`
- Elapsed time: `8.452999999979511` seconds
- Write-freeze time: `4.155999999959022` seconds
- Quiescence enforced, source unchanged, sentinel and permissions preserved,
  app semantics verified, and all disposable resources cleaned up
- Measured data-loss rows and data-loss window: zero
- Suggested targets only: RPO `3600` seconds and RTO `14400` seconds;
  neither is an approved production objective

Release rollback remains backup/restore only. Alembic downgrade is not a
release rollback mechanism.

## Task00-20 Inventory

The machine manifest records every path below as Git-tracked with its exact
byte count and SHA-256. Repeated documents are intentional where one acceptance
record covers multiple tasks.

| Task | Tracked evidence |
| --- | --- |
| 00 | `docs/acceptance/reset-baseline.md` |
| 01 | `docs/DragonRadar_开发需求PRD.md`; `docs/contracts/workbench-api.md`; `docs/superpowers/specs/2026-09-20-prd-reset-decisions.md` |
| 02-06 | `docs/acceptance/reset-api-navigation-local.md` |
| 07 | `docs/acceptance/reset-ingestion.md` |
| 08 | `docs/acceptance/reset-publication.md` |
| 09 | `docs/acceptance/reset-market.md` |
| 10 | `docs/acceptance/reset-role-contracts.md` |
| 11 | `docs/acceptance/reset-role-publication.md` |
| 12 | `docs/acceptance/reset-leaders.md` |
| 13 | `docs/acceptance/reset-analysis.md` |
| 14 | `docs/acceptance/reset-news.md` |
| 15-16 | `docs/acceptance/reset-regulation-rules.md` |
| 17-19 | `docs/acceptance/reset-base.md` |
| 20 | `docs/acceptance/reset-base.md` and the tracked Task20 manifest, summary, native output, raw performance, and performance summary |

Task20 implementation provenance is
`e29cf5bc242f7cb518ed30a25e96b57c05512f5e`. Its committed bundle-manifest
SHA-256 is
`2d1cd5c328db3f8055fa98ba23bcd476c172a116d5994a7e0b5e2c3fcfcd5740`.
These hashes establish exact committed bytes; they do not claim cryptographic
attestation by a separately trusted authority.

## Limitations

- Performance evidence uses 100 synthetic securities, 61 sessions, warmup,
  and three measured 20-concurrent rounds. OS and PostgreSQL caches were not
  controlled, so no cold-cache or licensed real-input result is claimed.
- Restore timing is one disposable local Docker drill, not an approved
  production RPO/RTO result.
- `secret_scan`, `python_dependency_scan`, `node_dependency_scan`, `sbom`, and
  `container_scan` are all absent and therefore production blockers.
- Licensed production input, official rules, real-model evaluation,
  public-sharing permission, and a 20-trading-day shadow run are absent.
- The older realtime feature formerly labeled Task21 is out of scope for this
  migration release audit.

## Blockers

| Blocker | Evidence owner | Evidence required |
| --- | --- | --- |
| Branch has no upstream or remote freshness proof | Release engineer | Configure the approved upstream and preserve fresh base/merge-base/ahead-behind evidence |
| 27 tracked dirty paths | Owners of those paths and release engineer | Resolve into reviewed commits or remove from the candidate without altering unrelated work |
| `licensed_real_input` | Data governance/provider owner | Separately trusted license and real-input execution evidence |
| `approved_official_rules` | Product/compliance owner | Approved rule set and authority record |
| `real_model_evaluation` | Model release owner | Real-provider evaluation with approved criteria |
| `public_sharing_permit` | Legal/compliance owner | Explicit public-sharing approval |
| `twenty_trading_day_shadow_run` | Operations owner | At least 20 real trading days of reviewed shadow evidence |
| Five supply-chain checks | Security/release engineering | Trusted scan reports for secrets, Python and Node dependencies, SBOM, and container image |

Task21.5 intentionally has no command-line option that lets a caller mark any
external or scan gate `VERIFIED`. Trusted-attestation integration requires a
separately reviewed change.

## Recheck

```powershell
uv run pytest tests/test_release_migrations.py tests/test_release_audit.py -q
uv run python scripts/verify_task20_bundle.py
uv run python scripts/audit_reset_release.py --check docs/acceptance/evidence/task21/release-manifest.json
uv run ruff check scripts/verify_release_migrations.py scripts/audit_reset_release.py tests/test_release_migrations.py tests/test_release_audit.py
uv run ruff format --check scripts/verify_release_migrations.py scripts/audit_reset_release.py tests/test_release_migrations.py tests/test_release_audit.py
uv run python -m compileall -q src tests scripts
```

No merge, tag, version bump, image publication, or deployment occurred.
