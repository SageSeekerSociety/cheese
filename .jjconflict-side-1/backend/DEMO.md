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
cd ../cheese-frontend
npm install       # first time only
npx vite --port 3000
```

Frontend: http://localhost:3000
Backend API: http://localhost:8081/docs

## Demo Data

Migration `219831eb75a3` seeds: 8 users, 10 topics, 3 teams, 3 spaces, 6 categories, 6 tasks, 5 questions, 4 answers, 4 knowledge entries — with member/admin relations, submission schemas, topic tags, etc.

All user passwords: `demo12345`
Usernames: alice, bobby, carol, david, evelyn, frank, grace, henry

## Seed Data Cheat Sheet

Useful for manually verifying space analytics / topics endpoints.

**Spaces** (`id → name, owner`):

| id | name             | owner  |
| -- | ---------------- | ------ |
| 1  | 人工智能实践空间 | alice  |
| 2  | 软件工程训练营   | bobby  |
| 3  | 数学建模工作坊   | carol  |

**Topics** (`id → name`): 1 深度学习 · 2 数据科学 · 3 Web 开发 · 4 算法与数据结构 · 5 计算机视觉 · 6 自然语言处理 · 7 云计算 · 8 开源项目 · 9 数据库 · 10 人工智能伦理

**Tasks ↔ Topics per space** (for topic popularity / analytics verification):

| space | task count | hot topics (desc by task count)       |
| ----- | ---------- | ------------------------------------- |
| 1     | 2          | 深度学习 (2), 计算机视觉 (2)          |
| 2     | 2          | Web 开发 (2)                          |
| 3     | 2          | 算法与数据结构 (1), 数据科学 (1)      |

## Reset

```bash
# Rollback seed data only (keeps tables)
uv run alembic downgrade a95752502bb0

# Re-seed
uv run alembic upgrade head
```

