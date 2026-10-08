# Reset Runtime

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

Requirements are Docker with Compose v2, PowerShell, and available local ports.

```powershell
.\scripts\start-reset.ps1
```

On first use, the script creates a random database password in
`.temp/reset-secrets/compose.env`. Windows ACLs are restricted to the current
user and SYSTEM. The credential is not printed and must not be committed. The
script validates Compose, builds images, starts PostgreSQL, runs migrations
explicitly, initializes the owner idempotently, and waits for readiness.
Repeated runs reuse the protected state and do not silently change ports.

To use different local ports on first startup:

```powershell
.\scripts\start-reset.ps1 -PublicPort 8088 -OwnerPort 8038
```

## Owner TLS Trust

Owner Caddy uses its internal TLS authority. Startup exports the public CA
certificate to `.temp/reset-secrets/owner-localhost-root.crt` but does not
install trust. Verify the displayed path and certificate fingerprint using
your local security process before installing it. Never bypass certificate
validation for owner pairing or normal use.

## Shutdown And Restart

```powershell
$envFile = ".temp/reset-secrets/compose.env"
docker compose --project-name dragon-radar-reset --env-file $envFile -f compose.reset.yaml stop
docker compose --project-name dragon-radar-reset --env-file $envFile -f compose.reset.yaml up -d --wait
```

Use `down` instead of `stop` to remove containers and networks. Do not add
`--volumes` unless permanent database and Caddy state should be deleted.

## Verification

```powershell
$envFile = ".temp/reset-secrets/compose.env"
docker compose --project-name dragon-radar-reset --env-file $envFile -f compose.reset.yaml config
docker compose --project-name dragon-radar-reset --env-file $envFile -f compose.reset.yaml ps
curl.exe -i http://127.0.0.1:8080/health/ready
curl.exe -i http://127.0.0.1:8080/api/snapshots/list
curl.exe -i -X POST http://127.0.0.1:8080/api/snapshots/list
uv run alembic heads
uv run pytest tests/test_reset_public_gateway.py -q
```

Expected public behavior is `200` for health and authorized published reads,
`403` for every write method, and `404` for owner, AI, administration,
ingestion, publication mutation, and private analysis-job paths.
