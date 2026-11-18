# PulseGrid Architecture

PulseGrid registers HTTP endpoints ("monitors"), probes them on a schedule, stores every
check result, opens an incident when a monitor fails N times in a row, resolves it when the
monitor recovers, sends signed webhooks on both transitions, and reports uptime over any window.

## 1. Component diagram

```
                 ┌──────────────────────────┐        ┌──────────────────────────┐
  HTTP clients ─►│  api/  (FastAPI)         │        │  cli/  (Typer)           │◄─ operators
                 │  auth · request-id mw    │        │  serve · worker · db     │
                 │  error → HTTP mapping    │        │  monitors · report       │
                 └────────────┬─────────────┘        └────────────┬─────────────┘
                              │      container.py (composition root, DI)      │
                              ▼                                               ▼
        ┌─────────────────────────────────────────────────────────────────────────────┐
        │ services/   MonitorService · CheckService · ReportingService                │
        │ workers/    Scheduler (poll due monitors → bounded concurrent checks)       │
        └──────┬──────────────────────┬──────────────────────┬────────────────────────┘
               │ ports (Protocols)    │                      │
               ▼                      ▼                      ▼
     ┌───────────────────┐  ┌───────────────────┐  ┌──────────────────────────────┐
     │ domain/           │  │ repositories/     │  │ integrations/                │
     │ entities, rules,  │  │ SQLAlchemy UoW +  │  │ HttpxProber · WebhookNotifier│
     │ Pydantic DTOs,    │  │ repositories      │  │ LoggingNotifier              │
     │ ports             │  └────────┬──────────┘  │ RedisLock · InMemoryLock     │
     └───────────────────┘           │             └───────┬───────────┬──────────┘
                                     ▼                     ▼           ▼
                           ┌──────────────────┐   ┌──────────────┐ ┌────────┐
                           │ PostgreSQL /     │   │ monitored    │ │ Redis  │
                           │ SQLite (Alembic) │   │ endpoints +  │ │(locks) │
                           └──────────────────┘   │ webhook sink │ └────────┘
                                                  └──────────────┘
   core/  config (pydantic-settings) · structlog logging · exception hierarchy · clock
```

Dependency rule: `domain` imports nothing from the project except `core`. `services` depend on
`domain` ports only. Concrete adapters (`repositories`, `integrations`) are wired together in
`container.py`, which is the only module that knows every layer. `api`, `cli` and `workers` call
services; they never touch SQLAlchemy or httpx directly.

## 2. Data flow

### Scheduled check (worker)

```
Scheduler.run_once
  └─ UoW: monitors.list_due(now)                    -- active AND next_check_at <= now
  └─ for each monitor (Semaphore(concurrency)):
       CheckService.check_monitor(monitor, force=False)
         ├─ lock.acquire("monitor:<id>")             -- skip if another worker holds it
         ├─ re-read monitor, skip if no longer due   -- guards against stale snapshots
         ├─ prober.probe(monitor)                    -- no DB transaction held during I/O
         ├─ UoW: monitor.record_outcome(outcome)     -- domain state machine
         │       checks.add(result)
         │       open incident   (UP/UNKNOWN → DOWN after failure_threshold failures)
         │       resolve incident (DOWN → UP on first success)
         │       commit
         ├─ notifier.notify(event) for each transition (after commit; failures logged)
         └─ lock.release
```

### Manual check (API `POST /api/v1/monitors/{id}/check` or CLI `monitors check`)

Same path with `force=True`; returns `409` when a worker currently holds the monitor lock.

## 3. Database schema

```
monitors
  id                    UUID        PK
  name                  VARCHAR(100) UNIQUE NOT NULL
  url                   VARCHAR(2048) NOT NULL
  method                VARCHAR(8)  NOT NULL      -- GET | HEAD
  interval_seconds      INTEGER     NOT NULL      -- 10 .. 86400
  timeout_seconds       FLOAT       NOT NULL      -- 0.5 .. 60, < interval
  expected_status       SMALLINT    NOT NULL      -- 100 .. 599
  failure_threshold     SMALLINT    NOT NULL      -- 1 .. 20
  is_active             BOOLEAN     NOT NULL
  status                VARCHAR(16) NOT NULL      -- unknown | up | down
  consecutive_failures  INTEGER     NOT NULL
  last_checked_at       TIMESTAMPTZ NULL
  next_check_at         TIMESTAMPTZ NOT NULL      INDEX ix_monitors_due (is_active, next_check_at)
  created_at, updated_at TIMESTAMPTZ NOT NULL

check_results
  id           UUID PK
  monitor_id   UUID FK → monitors.id ON DELETE CASCADE
  checked_at   TIMESTAMPTZ NOT NULL               INDEX (monitor_id, checked_at)
  success      BOOLEAN NOT NULL
  status_code  INTEGER NULL
  latency_ms   FLOAT NOT NULL
  error        VARCHAR(500) NULL

incidents
  id           UUID PK
  monitor_id   UUID FK → monitors.id ON DELETE CASCADE  INDEX
  opened_at    TIMESTAMPTZ NOT NULL
  resolved_at  TIMESTAMPTZ NULL
  cause        VARCHAR(500) NOT NULL
```

All timestamps are stored and returned in UTC.

## 4. Domain rules

* A new monitor starts `unknown` and is due immediately.
* Success → `consecutive_failures = 0`, status `up`; if it was `down`, the open incident resolves.
* Failure → `consecutive_failures += 1`; reaching `failure_threshold` while not `down` sets
  `down` and opens exactly one incident.
* A probe succeeds only when the response status equals `expected_status`. Timeouts and
  transport errors are failures with `status_code = null`.
* After every check `next_check_at = checked_at + interval_seconds`.
* Targets on loopback, private, link-local or reserved IP literals and `localhost` are rejected
  unless `PULSEGRID_ALLOW_PRIVATE_TARGETS=true`. (Hostnames are not resolved at registration
  time; put the worker on an egress-restricted network for full SSRF protection.)

## 5. REST API contract (`/api/v1`, header `X-API-Key` required)

| Method | Path                               | Body / query                          | Success              |
|--------|------------------------------------|---------------------------------------|----------------------|
| POST   | `/monitors`                        | `MonitorCreate`                       | 201 `MonitorRead`    |
| GET    | `/monitors`                        | `limit` (1-500, 100), `offset` (≥0)   | 200 `MonitorRead[]`  |
| GET    | `/monitors/{id}`                   |                                       | 200 `MonitorRead`    |
| PATCH  | `/monitors/{id}`                   | `MonitorUpdate` (partial)             | 200 `MonitorRead`    |
| DELETE | `/monitors/{id}`                   |                                       | 204                  |
| POST   | `/monitors/{id}/check`             |                                       | 200 `CheckResultRead`|
| GET    | `/monitors/{id}/checks`            | `limit` (1-1000, 50)                  | 200 `CheckResultRead[]` |
| GET    | `/monitors/{id}/uptime`            | `window_hours` (1-8760, 24)           | 200 `UptimeReportRead` |
| GET    | `/monitors/{id}/incidents`         | `limit` (1-500, 50)                   | 200 `IncidentRead[]` |
| GET    | `/incidents`                       | `open_only` (bool, false), `limit`    | 200 `IncidentRead[]` |

Unauthenticated: `GET /healthz` (liveness) and `GET /readyz` (database reachable).

Errors share one envelope: `{"error": {"code": str, "message": str, "request_id": str}}`.

| Exception                   | HTTP | CLI exit |
|-----------------------------|------|----------|
| `ValidationFailedError`     | 422  | 2        |
| `AuthenticationError`       | 401  | 1        |
| `NotFoundError`             | 404  | 4        |
| `ConflictError`             | 409  | 5        |
| `InfrastructureError`       | 503  | 1        |
| other `PulseGridError`      | 500  | 1        |

## 6. CLI contract (`pulsegrid ...`)

```
serve [--host H] [--port P]                 start the REST API (uvicorn)
worker [--once]                             run the scheduler loop (or a single pass)
db upgrade [REVISION]                       apply Alembic migrations (default: head)
db downgrade REVISION                       roll back migrations
monitors add NAME URL [--method] [--interval] [--timeout] [--expected-status] [--failure-threshold]
monitors list [--limit] [--offset]
monitors show ID
monitors pause ID | resume ID
monitors remove ID
monitors check ID                           probe now and print the result
report uptime ID [--hours]
report incidents [--monitor ID] [--open-only] [--limit]
prune --days N                              delete check results older than N days
```

All commands print JSON to stdout and errors to stderr.

## 7. Webhook contract

`POST <PULSEGRID_WEBHOOK_URL>` with `Content-Type: application/json`:

```json
{
  "event": "incident.opened | incident.resolved",
  "incident": {"id": "...", "monitor_id": "...", "opened_at": "...", "resolved_at": null, "cause": "..."},
  "monitor": {"id": "...", "name": "...", "url": "...", "status": "down"}
}
```

When `PULSEGRID_WEBHOOK_SECRET` is set the request carries
`X-PulseGrid-Signature: sha256=<hex HMAC-SHA256 of the raw body>`.
