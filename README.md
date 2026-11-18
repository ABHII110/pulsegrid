# PulseGrid

Uptime monitoring service. PulseGrid probes your HTTP endpoints on a schedule, records every
result, opens an incident when an endpoint fails several times in a row, resolves it when the
endpoint recovers, sends signed webhooks on both transitions, and reports uptime over any window.

- **REST API** (FastAPI) with API-key auth, request IDs and a uniform JSON error envelope
- **CLI** (Typer) for every operation, printing JSON for scripting
- **Worker** that checks due monitors with bounded concurrency; Redis locks let several replicas run safely
- **Storage** in PostgreSQL (production) or SQLite (single node, zero setup), migrated with Alembic

Design, schema and full contracts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (local, SQLite)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env            # then set PULSEGRID_API_KEY (see below)
uv run pulsegrid db upgrade
uv run pulsegrid monitors add example https://example.com --interval 60
uv run pulsegrid worker         # in one terminal
uv run pulsegrid serve          # in another terminal
```

Then open http://127.0.0.1:8000 for the landing page or http://127.0.0.1:8000/docs for the
interactive API docs.

Generate an API key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Running with Docker Compose (PostgreSQL + Redis)

Set `POSTGRES_PASSWORD` and `PULSEGRID_API_KEY` in `.env`, then:

```bash
docker compose up --build -d
```

Compose runs migrations once, then starts the API on `127.0.0.1:8000` and a worker. Scale workers
with `docker compose up -d --scale worker=3`; Redis locks keep them from probing the same monitor.

## Deploying to Render (free, single service)

`render.yaml` describes one free Docker web service that runs the API, the landing page and the
probe worker together (`PULSEGRID_EMBEDDED_WORKER=true`), applies migrations on start, keeps
`/docs` public and generates the API key for you.

1. Sign in at [render.com](https://render.com) with GitHub and give Render access to this repository.
2. **New → Blueprint**, pick the repository, and apply it.
3. Open the service's **Environment** tab to copy the generated `PULSEGRID_API_KEY`.

Free-plan trade-offs: the service sleeps when idle (probing pauses and the next visit loads slowly),
and the SQLite database lives on temporary disk, so monitors reset on every restart or deploy. For
durable data, create a Render PostgreSQL database and set `PULSEGRID_DATABASE_URL` to its connection
string; plain `postgres://` URLs are accepted.

## Using the API

Every `/api/v1` route needs the `X-API-Key` header.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/monitors \
  -H "X-API-Key: $PULSEGRID_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "example", "url": "https://example.com", "interval_seconds": 60}'

curl -H "X-API-Key: $PULSEGRID_API_KEY" "http://127.0.0.1:8000/api/v1/monitors/<id>/uptime?window_hours=24"
```

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/monitors` | Register a monitor |
| GET | `/api/v1/monitors` | List monitors (`limit`, `offset`) |
| GET / PATCH / DELETE | `/api/v1/monitors/{id}` | Read, partially update (incl. `is_active`), delete |
| POST | `/api/v1/monitors/{id}/check` | Probe now |
| GET | `/api/v1/monitors/{id}/checks` | Recent results |
| GET | `/api/v1/monitors/{id}/uptime` | Uptime over `window_hours` |
| GET | `/api/v1/monitors/{id}/incidents` | Incidents for one monitor |
| GET | `/api/v1/incidents` | All incidents (`open_only`) |
| GET | `/healthz`, `/readyz` | Liveness and database readiness (no auth) |
| GET | `/` | Landing page (no auth) |

Errors always look like `{"error": {"code", "message", "request_id"}}`; status codes are 401, 404,
409, 422, 503 or 500.

## CLI

```
pulsegrid serve | worker [--once] | prune --days N
pulsegrid db upgrade [REV] | db downgrade REV
pulsegrid monitors add NAME URL [--method --interval --timeout --expected-status --failure-threshold]
pulsegrid monitors list | show ID | pause ID | resume ID | remove ID | check ID
pulsegrid report uptime ID [--hours] | report incidents [--monitor ID] [--open-only]
```

Exit codes: `0` ok, `1` general or infrastructure error, `2` invalid input, `4` not found, `5` conflict.

## How monitoring works

- A monitor is healthy when the response status equals `expected_status` within `timeout_seconds`.
  Only headers are read; redirects are **not** followed (expect 301/302 explicitly if needed).
- After `failure_threshold` consecutive failures it goes **down** and one incident opens.
  The first success afterwards marks it **up** and resolves the incident.
- Each transition triggers a notification: a webhook when `PULSEGRID_WEBHOOK_URL` is set, otherwise a
  structured log line. Webhooks carry `X-PulseGrid-Signature: sha256=<HMAC of the body>` when
  `PULSEGRID_WEBHOOK_SECRET` is set. A failed webhook is logged and never blocks checking.
- Prune history periodically, e.g. a daily `pulsegrid prune --days 90`.

## Configuration

All settings are environment variables prefixed with `PULSEGRID_` (or entries in `.env`).
See [.env.example](.env.example) for the full list with defaults. Key ones:

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./pulsegrid.db` | Use `postgresql+asyncpg://...` in production |
| `API_KEY` | unset | Required for `serve`; at least 24 characters |
| `REDIS_URL` | unset | Enables cross-process locks for multiple workers |
| `ALLOW_PRIVATE_TARGETS` | `false` | Allow loopback/private IP targets |
| `WORKER_CONCURRENCY` | `20` | Max probes in flight per worker |
| `WEBHOOK_URL`, `WEBHOOK_SECRET` | unset | Incident webhooks |
| `ENVIRONMENT` | `development` | `production` hides `/docs` and `/openapi.json` |
| `EXPOSE_DOCS` | `false` | Keep the docs public in production |
| `EMBEDDED_WORKER` | `false` | Run the worker inside the API process |
| `MIGRATE_ON_START` | `false` | Apply migrations when `serve` starts |

## Landing page

`GET /` serves a single self-contained page (`src/pulsegrid/web/index.html`) with a WebGL planet
built on Three.js r0.143. The golden pings on the globe stand for probes. It loads Three.js, Lenis
and Inter from public CDNs and the planet models and cloud texture from an external storage
bucket, so it needs internet access in the browser. The page content still shows if WebGL or
those downloads fail.

**Explore space** (the button bottom-right, or the nav link on wide screens) turns the background
into an interactive map: scroll or pinch to zoom, drag to rotate, Esc to exit. Zooming in reveals
continent, country and city names (placed to about 1°, so roughly 100 km); zooming out shows a
stylised solar system and then a Milky Way model with a "You are here" marker. A HUD shows the
altitude or range, latitude/longitude under the reticle, and a zoom rail with quick-jump stops.
Distances outside Earth orbit are illustrative, not to scale. In `production` the page's links to `/docs` and `/openapi.json` return 404
because those routes are disabled there.

## Security notes

- No secrets in code; keys and URLs with credentials are `SecretStr` and never logged.
- API keys are compared in constant time.
- Monitor targets on loopback, private, link-local or reserved IP literals and `localhost` are
  rejected by default. Hostnames are not resolved at registration, so for full SSRF protection run
  workers on a network that cannot reach internal services.
- The container runs as a non-root user and binds the API to localhost in Compose.

## Development

```bash
make check        # ruff lint + format check, mypy --strict, pytest
make cov          # tests with coverage (gate: 90%)
make format       # auto-fix lint and format
```

Without `make` (e.g. Windows), run the commands directly:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Tests use temporary SQLite databases and fake network transports, so they need no services.
CI (`.github/workflows/ci.yml`) also runs migrations and a worker pass against PostgreSQL and builds
the Docker image.

### Project layout

```
src/pulsegrid/
  core/          config, logging, exceptions, clock
  domain/        entities + state machine, Pydantic DTOs, ports (Protocols)
  db/            engine, ORM tables, custom types, migration helpers
  migrations/    Alembic environment and revisions
  repositories/  SQLAlchemy repositories + unit of work
  services/      monitor, check and reporting services
  integrations/  httpx prober, webhook/log notifiers, Redis/in-memory locks
  workers/       scheduler + worker process runner
  api/           FastAPI app, routes, auth, middleware, error mapping
  cli/           Typer commands
  web/           landing page (single HTML file: Three.js planet, served at /)
  container.py   composition root (dependency injection)
```

New migration after changing `db/tables.py`:

```bash
uv run alembic revision --autogenerate -m "describe change"
```
