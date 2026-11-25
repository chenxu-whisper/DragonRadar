# Remaining requirements: integration checkpoint

This is an implementation checkpoint, not final acceptance or a release.
All current work is unstaged on `feature/all-requirements-20261124`; the main
workspace and existing services are preserved.

Actual application factories expose exact daily archive GETs, static snapshot
lineage before UUID matching, and immutable index history. Public routing omits
private AI, research and LHB namespaces. Owner routes additionally support
authenticated, CSRF-protected promotion/revocation and research metadata.
Caddy patterns were updated; actual gateway validation remains pending.

The clock details and reviewed daily archive entrypoints load lazily and bind
to the selected PUB/date. Browser acceptance time belongs to the client;
missing provider clocks remain unavailable. Private analysis commands preserve
their idempotency key across unknown POST outcomes, stop polling after
navigation/PUB/principal/denial changes, and reject late completions.

Local commands:

- `dragon-radar supplier-compare --help`: dedicated offline paired archive
  validator. Real independence/precision approval remains unavailable without
  an independently authenticated evidence authority.
- `dragon-radar ai-output --help`: output entitlement registry and offline
  `validate-summary-selections`. Selector validation does not promote a private
  task or authorize public output.

Receipts are in `.temp/`: topology 17 passed; CLI 3 passed; client 11 passed;
expanded analysis browser 11 passed plus PUB-switch 1 passed; clock/archive
entrypoints 3 passed; scoped shared Python Ruff passed.

Subsequent observed evidence on the current integration:

- `parent-summary-admission-final-green.log`: 22 passed on disposable
  PostgreSQL schemas, covering exact selected summary publication/replay,
  frozen old/new reads, sidecar erasure preserving hashes, forged-text
  rejection, additive upgrade and analysis admission.
- `parent-summary-topology-pure.log`: 14 passed for news selection identity,
  wire fields, local CLI and actual application boundaries.
- `parent-analysis-jobs-all-browser-current.log`: 15 passed for comparison
  cached reads, uncertain same-key retry, denial, principal/navigation/PUB
  changes, late responses, clocks and public daily archive entry.
- `parent-research-deployment-green.log`: 9 passed. Owner deployment,
  worker repository and production loader use the same configurable result
  version, defaulting to v2; explicit v1 and frozen prior runs remain distinct.

These are scoped receipts, not final source freeze or whole-project acceptance.
Migration/catalog/manifest readiness, specialized B3/B4 clocks, the five B8
scenes and consolidated reviews/fixes have since been implemented and verified
within their recorded scopes. The actual five builds exposed owner-module
isolation and entry-size issues; both were repaired without changing budgets.
The final source-bound 19-gate run and delivery verification remain pending.
Browser suites run serially and separately from the full database regression.
Genuine provider/rights/rules/evaluations/20 trading days and remote CI remain
separate evidence gates.
