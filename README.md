# cheese-backend-py

Python rewrite of the Cheese backend using FastAPI, SQLAlchemy 2.x, and Pydantic v2.

## Quick start (with uv)

```bash
cd cheese-backend-py
# install deps
uv sync
# apply the latest DB migrations (requires DATABASE_URL)
uv run alembic upgrade head
# run dev server
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

## Database migrations

Alembic lives under `migrations/` and reuses the same `DATABASE_URL`
value that the application uses. If the URL relies on the async driver
(`postgresql+asyncpg://`), the migration environment automatically
switches to psycopg2 so Alembic can run synchronously.

Common commands:

```bash
# create a new revision
uv run alembic revision -m "short description"

# apply pending migrations
uv run alembic upgrade head
```

The first revision (`1c64f1712118`) creates the `ai_user_quota` table
used by the AI advice quota service.
