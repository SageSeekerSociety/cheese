# Cheese Backend (Python)

Python FastAPI backend migrated from NestJS (cheese-backend) and Kotlin Spring Boot (cheese-backend-nt).

## Tech Stack

- **Web Framework**: FastAPI
- **ORM**: SQLAlchemy 2.x (async)
- **Database**: PostgreSQL 16
- **Configuration**: Pydantic v2 + pydantic-settings
- **Task Queue**: taskiq (Redis)
- **Package Manager**: uv

## Quick Start

```bash
# Install dependencies
uv sync

# Run database migrations
uv run alembic upgrade head

# Start server
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres" \
uv run uvicorn app.main:app --host 0.0.0.0 --port 8081

# Run tests
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres" \
RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration/ -v
```

## Project Structure

```
app/
├── api/routes/          # FastAPI routes
├── auth/                # Authentication & authorization
├── core/                # Config, errors, storage
├── db/                  # Database session
├── domain/              # Business domain modules
│   ├── answers/
│   ├── attachment/
│   ├── avatars/
│   ├── comments/
│   ├── discussion/
│   ├── groups/
│   ├── knowledge/
│   ├── llm/
│   ├── materials/
│   ├── notification/
│   ├── project/
│   ├── questions/
│   ├── space/
│   ├── task/
│   ├── team/
│   ├── topics/
│   └── user/
└── middleware/          # Middleware
```

## Test Status

| Type | Count | Status |
|------|-------|--------|
| Integration Tests | 636 | ✅ All Passing |

## Migration Status

| Phase | Status |
|-------|--------|
| Phase 0: Infrastructure | ✅ Complete |
| Phase 1: Contract Tests | ✅ Complete |
| Phase 2: Domain Migration | ✅ Complete |
| Phase 3: Cross-cutting Concerns | ✅ Complete |
| Phase 4: Production Deployment | ⏳ Pending |

See `PYTHON_MIGRATION_GUIDE.md` in the project root for detailed migration documentation.

## Port Configuration

| Service | Port |
|---------|------|
| cheese-backend-py | 8081 |
| cheese-backend (legacy NestJS) | 7777 |
| cheese-backend-nt (legacy Kotlin) | 8080 |
| PostgreSQL | 5432 |
| Redis | 6379 |

## Database Migrations

Alembic configuration is in `migrations/` directory, using `DATABASE_URL` environment variable.

```bash
# Create new migration
uv run alembic revision -m "short description"

# Apply migrations
uv run alembic upgrade head
```
