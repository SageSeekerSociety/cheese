# Cheese Backend (Python)

The ONE merged backend: 知是/Cheese product domains (spaces, tasks, teams,
questions, knowledge, …) at the root paths + the cheesex AI layer (projects,
topics, blocks, agent, memory, …) under `/api/*`. Migrated/unified from the
legacy NestJS (`cheese-backend`) and Kotlin (`cheese-backend-nt`) services,
which are retired.

## Tech Stack

- **Web Framework**: FastAPI
- **ORM**: SQLAlchemy 2.x (async)
- **Database**: PostgreSQL 16
- **Configuration**: Pydantic v2 + pydantic-settings
- **Task Queue**: taskiq (Redis)
- **Package Manager**: uv

## Quick Start

Setup and the everyday run live in the [repo README](../README.md#quick-start) —
one infra stack (`docker compose up -d` from the repo root) and `task dev`
(backend :8081, frontend :3000). This file used to document a competing flow on
:8799/:5200 against a second Postgres of its own, which is how the repo ended up
with two READMEs describing two setups that could not both be right.

From this directory:

```bash
uv run alembic upgrade head     # migrate (reads DATABASE_URL from .env)
task be:dev                     # or: uv run uvicorn app.main:app --port 8081 --reload
uv run pytest tests/ -n 4       # per-worker DBs on the :5433 test server
```

**Seeded demo (optional, separate ports).** `scripts/dev/up.sh` brings up a
backend on :8799 with vite on :5200, seeded with demo data — deliberately beside
`task dev` rather than instead of it. Point `DATABASE_URL` at `fusion_test` and
run `scripts/dev/db-reset.sh` first.

## Project Structure

```
app/
├── api/routes/          # FastAPI routes (auto-discovered — every APIRouter here is mounted)
├── auth/                # Authentication & authorization
├── core/                # Config, errors, db engine (the ONE pool), storage
├── db/                  # Session compat re-exports (→ app.core.db)
├── domain/              # Business domains: 知是 product (space, task, team,
│                        # questions, knowledge, …) + cheesex AI layer (agent,
│                        # topic, block, project, memory, workspace, …)
└── middleware/          # Middleware
```

## Database Migrations

Alembic configuration is in `alembic/`, using the `DATABASE_URL` environment
variable.

```bash
# Create new migration
uv run alembic revision -m "short description"

# Apply migrations
uv run alembic upgrade head
```
