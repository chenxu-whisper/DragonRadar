# Reset Runtime

Run every command below from the project root. This is the single current
guide for public reads and local owner/private review access.

The Reset stack has two independent entries:

- Public read entry: `http://127.0.0.1:8080`
- Local owner entry: `https://localhost:8031`

The starter deliberately provides no automatic public internet exposure. The
public port binds to loopback. Publishing it through a firewall, tunnel, DNS
record, or external reverse proxy is a separate operator decision.

## Access Model

The public API serves only published Reset read models. Every source dependency
must have a current approved grant with a nonempty license reference, valid time
window, and `sharing: true`. `local_display`, `local_storage`, `export`, and
`ai` do not imply sharing. Revoking sharing removes the affected publication
from public lists and blocks direct public reads immediately.

The owner API retains local owner sessions, review, AI, and private analysis
operations. It listens on `127.0.0.1:8001` inside the owner Caddy network
namespace and has no directly published host port. Public containers have no
network or DNS path to that namespace.

The public edge uses the fixed `172.30.19.0/28` Compose subnet: Caddy is
`172.30.19.2` and the API is `172.30.19.3`. The API trusts the dedicated
`X-Dragon-Client-IP` header only when its direct peer is Caddy's `.2` address.
Caddy removes inbound `Forwarded`, `X-Forwarded-*`, `X-Real-IP`, and
`X-Dragon-*` values before setting that one header from the actual remote host.
Change the subnet only as one reviewed Compose, Caddy, API, test, and
documentation update.

External data permits and model permits remain blocked until configured
explicitly. Startup does not import data, register source grants, configure an
AI provider, or enable a model token.

## Start

Requirements are Docker with Compose v2, Windows PowerShell 5.1, and available local ports.
Both entry scripts also accept execution from PowerShell 7: before touching
private state they hand off to `powershell.exe -NoProfile -File`, forwarding
only their declared parameters and returning its exit code. The ACL helpers
continue to run under Windows PowerShell 5.1. If that host is unavailable,
the entry stops with an installation instruction and a nonzero exit code.
The sole daily startup entry is `scripts/start-reset.ps1`. The root
`compose.yaml` replaces the former layered stacks and contains seven services:
`db`, `public-api`, `public-caddy`, `owner-api`, `owner-caddy`,
`analysis-worker`, and `ai-worker`. Frontend builds and gateway configuration
use only `containers/public.Dockerfile`, `containers/public.Caddyfile`,
`containers/owner.Dockerfile`, and `containers/owner.Caddyfile`; the backend
build still uses the root `Dockerfile`.

```powershell
.\scripts\start-reset.ps1
```

To update an already running instance from the current source:

```powershell
powershell.exe -NoProfile -File .\scripts\start-reset.ps1
```

The update rebuilds both frontends and the backend, runs migrations using a
temporary `analysis-worker` container on the worker data network, then waits
for the seven services. This avoids allocating the running public API's fixed
edge address to the migration container. Existing credentials, database and
owner CA volumes are reused.

On first use, the script creates a random database password in
`.temp/reset-secrets/compose.env`. Windows ACLs are restricted to the current
user and SYSTEM. The credential is not printed and must not be committed. The
script validates Compose, builds images, starts PostgreSQL, runs migrations
explicitly, initializes the owner idempotently, and waits for readiness.
Repeated runs reuse the protected state and do not silently change ports.
Keep `compose.env` whenever the database volume is retained. Do not copy it
into the frontend, print it, send it to chat, or share it with test instances.
File ACLs are not disk encryption; local system and Docker administrators
still have access. Changing the saved password is not PostgreSQL password
rotation. Startup does not migrate records from an old independent instance.

To use different local ports on first startup:

```powershell
.\scripts\start-reset.ps1 -PublicPort 8088 -OwnerPort 8038
```

## Owner TLS Trust

Owner Caddy uses its internal TLS authority. Startup exports the public CA
certificate to `.temp/reset-secrets/owner-localhost-root.crt` but does not
install trust. Display its path and SHA256 without exporting a private key:

```powershell
.\scripts\reset-owner.ps1 -Action Certificate
```

Verify the certificate, fingerprint, and purpose using your local security
process before explicitly approving a current-user trust-store change.
Never bypass browser warnings or TLS validation, or enter private information
over HTTP. After approval, the local user can install this exact certificate:

```powershell
$certificate = Resolve-Path .temp/reset-secrets/owner-localhost-root.crt
certutil -user -addstore Root $certificate
```

To revoke trust, inspect the SHA1 fingerprint and remove only that certificate:

```powershell
certutil -dump .temp/reset-secrets/owner-localhost-root.crt
certutil -user -delstore Root "<verified SHA1 fingerprint for this certificate>"
```

Do not delete certificates by name or assume an older independent instance's
CA is the Reset CA. Internal TLS health checks do not prove browser trust.

## Owner Pairing And Revocation

The owner controls read the same `.temp/reset-secrets/compose.env` as startup.
After verifying TLS trust, issue a one-time pairing code:

```powershell
powershell.exe -NoProfile -File .\scripts\reset-owner.ps1 -Action Pair
```

The code is written only to a protected local file under `.temp/reset-secrets/`;
use it within five minutes at the configured `https://localhost:8031` entry
(or the saved custom owner port). Do not print the code, put it in a URL or
clipboard, or send it to chat. A new issuance invalidates older unused codes.
Owner sessions last eight hours, then require pairing again.

At the owner entry, the locked page shows the command above, the protected
`.temp/reset-secrets/pair-*.txt` location, and the current entry URL, including
custom ports. Run the command locally from the project root, open the exact
file path it reports, and enter its code in the pairing field. The page does
not read local files or issue codes. An empty field cannot be submitted, and
an in-flight submission cannot be repeated.

If pairing is rejected, the page clears the field and offers the same local
issuance steps. The service returns a general refusal; the page does not
claim to know whether a code expired, was consumed, or was replaced. Issue a
fresh code and enter it once within five minutes at the trusted owner URL.
Session expiration locks private content; pairing again as the same owner
can restore drafts held in page memory. Explicit logout clears that memory.
Use `Revoke` only when you intend to invalidate all owner sessions and
unused codes; it is not the normal session-recovery step.

```powershell
.\scripts\reset-owner.ps1 -Action Revoke
```

Revocation invalidates owner sessions and unused pairing codes, not saved
notes, watchlists, or snapshots. The page hides private content after session
invalidation; unsaved drafts may remain locked in page memory for same-owner
re-pairing. Explicit logout, closing, or reloading can discard unsaved input.
Reset notes, watchlists, and simulation records use local IndexedDB partitioned
by profile and origin; server volumes are not a backup of browser-local data.
Old standalone private database or browser records are not automatically
imported into this instance.

## Shutdown And Restart

```powershell
docker compose --project-name dragon-radar-reset --env-file .temp/reset-secrets/compose.env -f compose.yaml stop
docker compose --project-name dragon-radar-reset --env-file .temp/reset-secrets/compose.env -f compose.yaml down
.\scripts\start-reset.ps1
```

Choose `stop` to retain containers, or `down` to remove containers and networks.
Neither deletes persistent volumes. Never add `-v` or `--volumes` for an
update, rollback, or restart. Preserve the database, CA private-key volumes,
and protected state; volumes are not backups. See
[database backup and restore](operations/database-backup-restore.md).

## Verification

```powershell
docker compose --project-name dragon-radar-reset --env-file .temp/reset-secrets/compose.env -f compose.yaml config --quiet
docker compose --project-name dragon-radar-reset --env-file .temp/reset-secrets/compose.env -f compose.yaml ps
curl.exe -i http://127.0.0.1:8080/health/ready
curl.exe -i http://127.0.0.1:8080/api/snapshots/list
curl.exe -i -X POST http://127.0.0.1:8080/api/snapshots/list
uv run alembic heads
uv run pytest tests/test_reset_public_gateway.py -q
```

Expected public behavior is `200` for health and authorized published reads,
`403` for every write method, and `404` for owner, AI, administration,
ingestion, publication mutation, and private analysis-job paths.
Use the saved public port when customized. `config --quiet` validates without
printing interpolated credentials; do not use plain `config` in shared output.

## Task20 Base Acceptance

Use only a disposable PostgreSQL database named `dragon_test`. The performance
test creates and removes an isolated schema and writes evidence under `.temp/`.
Load `DRAGON_TEST_DATABASE_URL` from your protected local test configuration;
never use the Reset database or print its password or connection string.

```powershell
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base.json
```

The default `full` mode is the only automated acceptance path. It executes
every required Task20 command from an immutable argument allowlist through the
production subprocess Runner, using argv arrays, `shell=False`, and bounded
timeouts. The Runner captures start/end timestamps, exit status, stdout, and
stderr. Each exact byte stream is written atomically and then bound by size and
SHA-256. Test counts come from captured command-native summaries; performance,
no-write, and asynchronous-work metrics come from the machine-readable
performance artifact. Callers cannot provide command results, exit statuses,
counts, logs, or gate statuses to this mode.

Evidence schema v2 is closed: gate IDs must be complete and unique, each
argv/cwd/environment class must exactly match its static specification,
required test counts must be positive, and failed/skipped counts must be zero.
Gate status and observations are derived by the verifier.

Use `--mode focused` for a shorter local diagnostic run. Because it omits full
backend and frontend gates, its overall automated status remains blocked.
Use `--validate-existing <path>` only to inspect an existing evidence bundle:

```powershell
uv run python scripts/verify_reset.py --validate-existing .temp/acceptance/task20-command-evidence.json --output .temp/acceptance/integrity.json
```

This mode checks schema, artifacts, hashes, command allowlist metadata, and
derived arithmetic, but it does not execute commands. Its report is always
`UNVERIFIED/INTEGRITY_ONLY`, automated acceptance is always `unverified`, and
its process exit is always nonzero even when integrity checks pass.

`tested_commit` must equal the current `HEAD`. It may equal `HEAD^` only when
the current commit changes paths exclusively under `docs/acceptance/`,
`evidence/`, or `.github/evidence/`; the validation report then records the
`HEAD^` relation. README or general runtime-document changes do not qualify.

In execution mode, exit `0` means all automated gates passed, while the
report's overall status still remains blocked until real input, official
rules, real model evaluation, public-sharing permission, and at least 20 real
trading-day shadow evidence are supplied. Exit `1` means executed evidence
failed; exit `2` means required executed evidence is missing. Integrity-only
mode uses nonzero exit `3` when its checks pass. CI uses
`scripts/acceptance-stack.sh` for its isolated container/TLS harness, runs
`--mode full`, and uploads `.temp/acceptance/` even on failure. The harness is
not an alternative daily startup entry and must not use local Reset secrets
or retained user volumes. Historical Task20/21 evidence retains its original
paths and bytes; it is not a current startup recipe.

Recovery behavior and observed synthetic metrics are recorded in
[`docs/acceptance/reset-base.md`](acceptance/reset-base.md).
Real-input and production work remains separately blocked as described in
[TODO](TODO.md) and the [production evidence contract](production-evidence.md).
