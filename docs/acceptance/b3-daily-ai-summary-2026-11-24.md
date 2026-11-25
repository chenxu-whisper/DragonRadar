# B3 / B4a Integration Handoff

Status: owned backend and isolated UI delivered; migration stable-ready.
Full end-to-end acceptance remains blocked on the parent-owned hooks below.
This is engineering evidence, not provider-quality, legal-rights, release or deployment approval.

## Migration

`0015_daily_ai` follows `0014_research`. Owner foreign keys resolve to
`owner_principals.id`. Fresh migration and prior-head upgrade with pre-existing
live-metadata tables pass. Guard SQL uses `jsonb_build_object`, not a SQLAlchemy
bind-ambiguous JSON literal. No prior migration bytes were edited.

Evidence: `.temp/b3-daily-ai/migration-green-attempt.log`,
`.temp/b3-daily-ai/db-first-full.log`.
Latest exact-current-file verification:
`.temp/b3-daily-ai/migration-owner-retention-final.log`, **5 passed in 16.79s**.
The migration now splits withdrawal INSERT guards by recognized sidecar archive
type. Original-domain bindings still invoke the unchanged legacy function.
Sidecar bindings enforce artifact/source/PUB identity, cutoff and absent seal;
later selected summaries also require exact archived news materialization bytes.
Machine-local readiness note: `.temp/b3-daily-ai/migration-ready.md`.

## Application Wiring

Public and local-read application state:

```python
from dragon_radar.public_daily_ai.repository import PublicDailyAIRepository
from dragon_radar.public_daily_ai.service import PublicDailyAIService
from dragon_radar.http.routes.daily_ai import install_daily_ai_routes

registry = PublicDailyAIRepository(
    database, publication_repository, clock=clock, public=is_public,
)
app.state.public_daily_ai_repository = registry  # News v3 derived-text gate
app.state.public_daily_ai_service = PublicDailyAIService(registry)
install_daily_ai_routes(app)
```

For PUBLIC, the injected publication repository must retain
`PublicSharingAccessPolicy`; do not replace it with local display policy.
The public registry never imports or queries `AITaskRepository`.

Private application only:

```python
from dragon_radar.ai.publication import AIPublicationService
from dragon_radar.http.routes.ai_publication import install_ai_publication_routes

app.state.ai_publication_service = AIPublicationService(
    ai_service.tasks, publication_repository, clock=clock,
)
install_ai_publication_routes(app)
```

Existing OwnerGate, owner session and CSRF middleware remain mandatory.
No owner route is installed in the public application.

GETs allowed at the public gateway:

- `/api/market/daily-ai`
- `/api/market/daily-ai/archives`

Private routes:

- POST `/api/ai/task/{task_id}/publish-daily`
- POST `/api/ai/task/{task_id}/publish-summary`
- POST `/api/ai/task/{task_id}/published-daily/{archive_id}/revoke`
- POST `/api/ai/task/{task_id}/published-summary/{summary_id}/revoke`
- GET `/api/ai/task/{task_id}/publication-eligibility?output_entitlement_reference=R`

Eligibility is advisory; the POST repeats independent verification and current
rights checks in its transaction.

## Dependencies And Retention

Daily AI inserts existing `source_dependencies` with:
`archive_type=public_daily_ai`, `archive_id=artifact UUID`,
`derived_type=daily_ai`, `derived_id=artifact UUID`, `publication_id=base PUB`.

Summary promotion inserts `archive_type=promoted_news_summary`,
`derived_type=news_summary`, and the same artifact/PUB binding.
Archives store the maximum assigned **existing dependency sequence**.
There is no new PUB domain or private task discovery index.

Required shared hook: base-PUB authorization must exclude these sidecar rows
from the five-domain input-identity comparison. The current shared repository
rejects the extra types after a first promotion. Do not weaken original-domain
identity checks or append a sixth domain.

Later selected-news publication must distinguish the additional
`promoted_news_summary` eligibility dependency from `news_article_revision`
identity dependencies. Validate it through the summary registry.

Retention must explicitly dispatch `daily_ai` and `news_summary`, not look them
up as `publication_results.domain`. After recording the original storage
withdrawal, cutoff-bound dependencies and binding seal, call:

```python
from dragon_radar.public_daily_ai.retention import erase_artifact
erase_artifact(
    connection, withdrawal_id=withdrawal.id,
    artifact_id=dependency["derived_id"],
    derived_type=dependency["derived_type"],
)
```

This uses the existing transaction and rejects absent/unsealed cutoff bindings.
The database trigger allows only the tombstone payload, preserving identity,
content hash, rights and audits. Display withdrawal blocks without erasure.
Restored sources do not automatically restore historically bound artifacts.
Summary references in later PUBs must also be cutoff-bound by the parent.

## News Publication And CLI

`PromotedSummaryMaterializer(news_service, registry, selections)` exposes:

- `selection_hash`: canonical identity to freeze into the publication command hash.
- `materialize_context(context, *, connection, topic_context=None)`: returns
  `DomainMaterialization`, delegates existing news rules, adds artifact/null to
  every article and emits schema version 3.
- `dependencies(context, *, topic_context=None)`: original article dependencies
  plus explicit selected-summary eligibility dependencies.

Selections are exactly `summary_id,summary_revision,article_id,article_revision_id,
article_revision`. No task ID, owner ID, promotion or generation is accepted.
Missing/mismatched selection fails; old v2 PUBs are never rewritten.

Local module CLI (no DB work during import/help):

```text
python -m dragon_radar.public_daily_ai.cli register --document license.json
python -m dragon_radar.public_daily_ai.cli revoke --reference R --idempotency-key K --reason-reference POLICY
python -m dragon_radar.public_daily_ai.cli validate-summary-selections --document selections.json
```

The last command is DB-free and outputs `summary_selections,selection_sha256`.
The parent central news CLI can reuse `validate_selections` and must include
`selection_sha256` in idempotency identity before any publication write.
Registration/revocation require explicit local operator execution and the
configured database; the agent has not run them against a production database.

## UI Wiring

Public default export:
`web/src/features/public-daily-ai/PublicDailyAI.tsx`.
Props: `snapshotId,date,onLocate?`. `onLocate(citation, returnFocus)` receives a
public PUB/pointer locator and the triggering element for focus restoration.
The feature imports no private client. PUB changes, read failures and
`dragon-source-revoked` clear body, citations and versions; parent should emit
that event for known public rights changes.

The default citation action mounts `PublicCitationSource.tsx`, re-reading the
exact archive and existing `/api/market/overview` endpoint. It resolves only the
actual `/input/market_overview/...` coordinate, compares its complete scalar
value with the archived fragment, and highlights it. No new gateway endpoint
or private task read is involved. Escape/close restores triggering focus.

Private default export: `web/src/features/ai/PublicationPanel.tsx`.
Props: `taskId,revision,snapshotId,date`. Mount it only through private dynamic
modules. `publication-client.ts` keeps owner cookie/CSRF writes, checks
server eligibility and preserves the exact command for uncertain-result retry.
It explicitly publishes and withdraws receipts; it never automatically accepts
or publishes.

News default export:
`web/src/features/news/PromotedSummaryPreview.tsx`, prop `preview`.
`validatePromotedSummaryPreview` is an exported exact seven-field validator
with Unicode code-point checks. Parent list/detail validation can call it, and
the news page can mount the component without N+1 reads.

Shared `App.tsx`, task panel and news page wiring remain parent-owned.

## Current Evidence

- Pure Python/API/CLI cases: 35 passed (`pure-final-second.log`).
- Public UI validator/generation/evidence/Unicode tests: 4 passed (`ui-owned-final.log`).
- `tsc --noEmit`: latest run blocked solely by TS18046 in parent-owned
  `web/tests/fixtures/b8-residual.ts` lines 77-97 (`typescript-final.log`).
- PostgreSQL fresh/upgrade, owner/CSRF, eligibility and sealed-erasure:
  5 passed (`migration-owner-retention-final.log`).
- Full owned PostgreSQL plus parent retention: 5 passed, 5 failed, zero skips
  (`full-owned-parent-retention-final.log`). Three failures are base-PUB
  sidecar dependency filtering; two are missing RetentionService dispatch.
- Existing AI verification regression: 40 passed
  (`ai-verification-with-secret-final.log`).
- Focused existing news regression: 6 passed, one exact-key assertion requires
  parent synchronization for approved `ai_preview`, `revision_id`, `received_at`
  fields (`news-regression-current.log`, `tests/test_reset_news.py:326`).
- Owned Ruff: zero warnings/errors (`ruff-final-second.log`).
- Owned compileall: exit 0 (`compile-final.log`), cache under this log directory.
- Mandatory utree flush attempted, exit 127: tool unavailable after unsupported
  cygwin bootstrap (`utree-flush.log`). Workflow report: `workflow.md`.
- Raw logs remain in `.temp/b3-daily-ai`.

Remaining gates: parent dependency filtering/retention dispatch, synchronized
news contract assertion, shared TypeScript errors, broader transaction/HTTP
regression, independent review, five-build physical isolation and UI acceptance.
No staging, commit, push, service operation or production DB operation performed.
Production stays disabled without genuine provider integration, at least 100
real independently reviewed evaluations, and genuine source/output sharing
rights. Synthetic passing tests do not establish those facts.
