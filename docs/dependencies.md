# Dependency Review

Review date: 2026-09-09. Scope: local data-foundation backend only.
Exact Python versions and hashes are recorded in `uv.lock`.
The tested PostgreSQL image reports 17.11 and is pinned by digest in Compose/CI.
The Python 3.12 Bookworm runtime image is also pinned by digest in `Dockerfile`.

| Dependency | Upstream license | Use |
| --- | --- | --- |
| FastAPI | MIT | Local read API, no provider network client |
| Pydantic / pydantic-core | MIT | Typed validation, no telemetry integration |
| SQLAlchemy | MIT | PostgreSQL persistence |
| psycopg / psycopg-binary | LGPL-3.0-only | PostgreSQL driver; dynamically loaded, unmodified |
| Alembic | MIT | Explicit schema migrations |
| Uvicorn | BSD-3-Clause | ASGI server, local development or container network |
| pytest | MIT | Development-only testing |
| HTTPX | BSD-3-Clause | Development-only ASGI/HTTP verification |
| Ruff | MIT | Development-only linting and formatting |
| pytest-cov / coverage | MIT / Apache-2.0 | Development-only coverage measurement |
| uv / uv_build | MIT OR Apache-2.0 | Package/build tooling |
| PostgreSQL | PostgreSQL License | Local persistent database |
| Python | PSF-2.0 | Runtime |

Direct dependency metadata was checked against PyPI's structured package metadata.
Installed transitive distribution metadata was also inspected: Starlette,
HTTPX/httpcore, Click, MarkupSafe and idna use BSD variants; AnyIO, h11, Mako,
annotated types and typing-inspection use MIT; greenlet uses MIT AND PSF-2.0;
typing_extensions uses PSF-2.0; tzdata uses Apache-2.0; certifi uses MPL-2.0.
Certifi is pulled by the development HTTP client, not used as a data license.
This is an engineering inventory, not approval to redistribute third-party data.
Keep wheel metadata/license files and bundled library notices when distributing
images. Psycopg's LGPL obligations and bundled libpq/OpenSSL notices require
particular attention for redistribution; this increment does not modify or
statically relink the driver. Transitive packages remain separately licensed;
the lock file, installed distribution metadata and image SBOM are the inventory
for a later distribution review.

Build-time network access is limited to configured package/image registries.
Runtime data-provider, AI, analytics telemetry and public tunnel integrations are
absent. The base Compose backend network is internal. No data vendor credentials
or user `.env` file are read by tests or shipped in the image.
The loopback development override adds a non-internal API network for Docker
Desktop port forwarding; it is not an outbound firewall. Database remains internal.

## Upgrade and Rollback

Review lock changes, licenses and compatibility before upgrading. Run all unit
and real PostgreSQL integration tests and rebuild the image. Database migration
rollback is a separate operation from application image rollback; back up before
upgrading an existing database. Do not remove persistent volumes to roll back.

No vulnerability-free or production-license-complete claim is made by this
inventory. Automated vulnerability scanning, image SBOM review and legal
redistribution review remain release gates before external distribution.
