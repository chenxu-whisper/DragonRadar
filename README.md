# DragonRadar

Local, traceable end-of-day market research. This increment implements the data
foundation and deterministic calculations, not the seven-page P0 application.
No real market provider, AI service, broker connection or public sharing is enabled.

## Development

Requirements: Python 3.12, uv 0.8.2 or compatible, Docker Compose v2.
From this worktree in PowerShell:

```powershell
$env:PYTHONPYCACHEPREFIX = "$PWD/.temp/pycache"
$env:UV_CACHE_DIR = "$PWD/.temp/uv-cache"
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m compileall -q src tests
```

Database integration tests require `DRAGON_TEST_DATABASE_URL` pointing to a
dedicated PostgreSQL test database. Never use a real research database for tests.
Tests without that variable do not constitute database acceptance.
When the test container already exists, use
`docker start dragon-radar-foundation-test-db` instead of creating it again.

```powershell
docker run --detach --name dragon-radar-foundation-test-db `
  --publish 127.0.0.1:55432:5432 `
  --env POSTGRES_DB=dragon_test --env POSTGRES_USER=dragon_test `
  --env POSTGRES_PASSWORD=dragon-test-local-only `
  postgres:17-alpine
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
uv run pytest -q --cov=dragon_radar --cov-report=term-missing
docker stop dragon-radar-foundation-test-db
```

The literal password above is exclusively for the isolated test database.
The normal Compose stack requires a separate strong password.

## Local Containers

On Windows, the startup script generates an owner-only credential file in
`.temp/local-compose.env`, builds, migrates and waits for readiness:

```powershell
.\scripts\start-local.ps1 -Port 8000
```

Keep that generated credential file while retaining the database volume. The
script does not read an existing project `.env`. To stop this script-managed stack:

```powershell
docker compose --env-file .temp/local-compose.env -f compose.yaml -f compose.dev.yaml stop
```

For manual setup instead of the script, use a separate fresh Compose project.
Generate a hexadecimal password with a password manager, or in PowerShell:

```powershell
$env:COMPOSE_PROJECT_NAME = "dragon-radar-manual"
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$env:DRAGON_DB_PASSWORD = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
docker compose build
docker compose up -d db
docker compose run --rm api migrate
docker compose -f compose.yaml -f compose.dev.yaml up -d
```

Persist the password in your local secret manager before closing the shell.
Subsequent starts must use the same database password; changing an environment
variable does not rotate an initialized PostgreSQL account.

The development read API is available at `http://127.0.0.1:8000`.
Set `DRAGON_API_PORT` before startup if that port is occupied.
The base Compose file exposes neither database nor API host ports. The development
override exposes API only on loopback; this is not an authenticated share server.
The base backend network is internal. The development override adds a network
for Docker Desktop loopback port forwarding; its API container has outbound
network capability, although no provider/model client is implemented. The
database remains attached only to the internal backend network.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Invoke-RestMethod http://127.0.0.1:8000/api/v1/snapshots
docker compose -f compose.yaml -f compose.dev.yaml stop
```

Startup does not insert demo data or publish snapshots. Use `dragon-radar --help`
and [the import contract](docs/data-import.md) for explicit local source registration, data import
and publication. Browser API methods cannot register sources, import, publish,
invoke AI or write notes.

## Data Rules

- Every snapshot records source, cutoff, pool/calendar/adjustment versions and
  explicit coverage. A missing historical date does not select today's snapshot.
- Source rights must permit the requested local operation and remain valid on
  read. Grant expiry or revocation blocks access to previously published data.
- Incomplete or invalid batches retain issues and cannot publish; no zero filling,
  incomplete-pool ranking or silent source blending.
- N-day returns require N+1 positions from the supplied trading calendar. Holiday
  calendars are not guessed. Missing/suspended history excludes the security.
- Research returns use one adjustment version. Closing limit checks use raw
  prices and supplied effective limit prices, never a hardcoded 10% assumption.
- Published snapshots are immutable; corrections create new revisions.
- Historical backfills are retrospective data, not contemporaneous evidence.

## Structure and Scope

`src/dragon_radar/domain` defines data and quality contracts;
`analytics` implements pure calculations; `storage` owns PostgreSQL transactions.
The local CLI administers data, and FastAPI exposes read models. Packaged migrations
live in `src/dragon_radar/migrations`; test artifacts and caches live in `.temp/`.

The first increment does not claim P0 release readiness. Pending work includes
frontend, approved role thresholds, actual data-source integration, regulatory
review, private notes, AI evaluation, owner operation contexts, Caddy/Tunnel,
backup recovery exercises, accessibility, and the required shadow-run period.

Database volumes are not backups. Do not run `docker compose down -v` to update
or roll back the application. Application rollback cannot undo incompatible
database migrations. Back up and test restoration before future upgrades.

See [the approved design](docs/superpowers/specs/2026-09-09-data-foundation-design.md),
[implementation plan](docs/superpowers/plans/2026-09-09-data-foundation.md),
[verification results](docs/verification-data-foundation.md),
[dependency inventory](docs/dependencies.md), and [product requirements](docs/leader_quant_prd.md).
