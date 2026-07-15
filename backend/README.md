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

```bash
# Infra: Postgres (host :5433) + Redis via docker
docker compose up -d          # from backend/
bash ../scripts/dev/db-reset.sh   # create + migrate the dev DB

# Backend on :8799 (and vite on :5200) — the fusion demo entrypoint
bash ../scripts/dev/up.sh
# or backend only:
bash ../scripts/dev/backend.sh

# Run the test suite (provisions per-worker DBs on the docker PG)
uv run pytest tests/ -n 4
```

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
