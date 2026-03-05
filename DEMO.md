# Demo Data Guide

## Quick Start

```bash
# 1. Start infra
docker compose up -d postgres valkey

# 2. Run migrations (schema + demo data)
uv run alembic upgrade head

# 3. Start backend
uv run uvicorn app.main:app --port 8081

# 4. Start frontend
cd reference/cheese-frontend
npm install   # first time only
npx vite --port 3000
```

Frontend: http://localhost:3000
Backend API: http://localhost:8081/docs

## Demo Data

Migration `219831eb75a3` seeds: 8 users, 10 topics, 3 teams, 3 spaces, 6 categories, 6 tasks, 5 questions, 4 answers, 4 knowledge entries — with member/admin relations, submission schemas, topic tags, etc.

All user passwords: `demo123`
Usernames: alice, bob, carol, david, eve, frank, grace, henry

## Reset

```bash
# Rollback seed data only (keeps tables)
uv run alembic downgrade a95752502bb0

# Re-seed
uv run alembic upgrade head
```
