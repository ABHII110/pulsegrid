# PulseGrid Build Progress

Each phase ended with `ruff check`, `ruff format --check`, `mypy --strict` and `pytest` passing.

## Phase 1 — Blueprint & Contracts ✅
- [x] `docs/ARCHITECTURE.md` (diagram, data flow, schema, API/CLI/webhook contracts)
- [x] `pyproject.toml`, `.env.example`, `Makefile`, `.gitignore`
- [x] `src/pulsegrid/__init__.py`, `py.typed`
- [x] `PROGRESS.md`

## Phase 2 — Core Infrastructure ✅
- [x] `core/config.py`, `core/logging.py`, `core/exceptions.py`, `core/clock.py`
- [x] `db/types.py`, `db/base.py`, `db/tables.py`, `db/session.py`, `db/migrate.py`
- [x] `migrations/` (async Alembic env + `r0001_initial`), `alembic.ini`
- [x] `Dockerfile`, `docker-compose.yml`, `.dockerignore`

## Phase 3 — Domain & Business Logic ✅
- [x] `domain/models.py`, `domain/schemas.py`, `domain/ports.py`
- [x] `repositories/sqlalchemy.py` (monitors, checks, incidents, unit of work)
- [x] `services/monitor_service.py`, `services/check_service.py`, `services/reporting_service.py`

## Phase 4 — Interfaces & Integrations ✅
- [x] `integrations/prober.py`, `integrations/notifiers.py`, `integrations/locks.py`
- [x] `container.py`
- [x] `workers/scheduler.py`, `workers/runner.py`
- [x] `api/app.py`, `api/deps.py`, `api/security.py`, `api/errors.py`, `api/middleware.py`
- [x] `api/routes/health.py`, `api/routes/monitors.py`, `api/routes/incidents.py`
- [x] `cli/main.py`

## Phase 5 — Testing, CI & Docs ✅
- [x] Unit tests: domain, schemas, config, exceptions, logging, integrations
- [x] Integration tests: database + migrations, repositories, services, scheduler, API, CLI, wiring
- [x] Coverage gate at 90% (currently ~98%)
- [x] `.github/workflows/ci.yml` (lint, mypy, tests on 3.12/3.13, PostgreSQL migrations, Docker build)
- [x] `README.md`

## Not verified locally
- Docker image build and Compose stack (Docker daemon was not running); covered by the CI `docker` job.
- PostgreSQL and Redis at runtime (tests use SQLite and fakes); PostgreSQL is covered by the CI `postgres` job.
