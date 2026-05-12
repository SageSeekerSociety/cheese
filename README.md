# Cheese

Monorepo for the Cheese community platform — Python/FastAPI backend + Vue 3 frontend.

## Prerequisites

| Tool | Install |
|------|---------|
| [uv](https://docs.astral.sh/uv/) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Node.js 22+](https://nodejs.org/) | via package manager or [nvm](https://github.com/nvm-sh/nvm) |
| [pnpm](https://pnpm.io/) | `corepack enable` (included with Node) |
| [Docker](https://docs.docker.com/get-docker/) | Docker Desktop or Docker Engine |
| [Taskfile](https://taskfile.dev/) | `sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b ~/.local/bin` |

**Windows users**: Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) first (`wsl --install`), then work inside WSL. All instructions below assume a Unix shell.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/SageSeekerSociety/cheese-backend-py.git
cd cheese-backend-py

# 2. Configure environment
cp backend/.env.example backend/.env    # edit if needed

# 3. One-command setup (install deps + start infra + run migrations)
task setup

# 4. Start development
task dev        # backend on localhost:8081
# In another terminal:
task dev:fe     # frontend on localhost:5173
```

Open http://localhost:5173 in your browser.

## Project Structure

```
cheese-backend-py/
├── backend/                 # Python/FastAPI backend
│   ├── app/                 # source code (routes → services → repositories → models)
│   ├── tests/               # unit + integration + contract tests
│   ├── migrations/          # Alembic database migrations
│   └── Dockerfile           # production image
├── frontend/                # Vue 3 / TypeScript frontend
│   ├── src/                 # source code
│   └── Dockerfile           # production image (nginx)
├── e2e/                     # Playwright E2E tests
├── deploy/                  # production deployment configs
├── docker-compose.yml       # dev infrastructure (PG, Redis, ES)
├── docker-compose.dev.yml   # optional: backend in Docker (Windows without WSL)
└── Taskfile.yml             # unified task runner
```

## Common Commands

```bash
task --list                  # see all available commands

# Backend
task be:check                # ruff + pyright + pytest
task be:test                 # run tests (incremental)
task be:lint                 # lint only
task be:db:migrate           # run migrations
task be:db:revision MSG="description"  # create migration

# Frontend
task fe:dev                  # start Vite dev server
task fe:check                # lint + typecheck + build
task fe:build                # production build

# Infrastructure
task infra                   # start PG/Redis/ES
task infra:down              # stop all
task infra:logs              # tail logs

# E2E
task e2e:test                # run Playwright tests
task e2e:test:headed         # with visible browser
```

## Development Environments

### Linux / macOS / WSL (recommended)

Everything runs natively — `uv run` for backend, `pnpm` for frontend, Docker for infrastructure only.

### Windows without WSL

Backend runs in Docker, frontend runs natively:

```bash
# Start infrastructure + backend in Docker
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Frontend still runs natively (Vite HMR doesn't work well in Docker)
cd frontend && pnpm dev
```

> **We strongly recommend WSL2 over this approach.** Docker volume performance on Windows causes slow hot-reload. WSL2 takes 5 minutes to install and gives a native Linux experience.

## Production Deployment

See [`deploy/`](deploy/) for production Docker Compose, environment template, and deploy script. CI/CD pushes images to ghcr.io and deploys via Tailscale SSH.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.x (async), Pydantic v2 |
| Frontend | Vue 3, TypeScript, Vuetify, Vite |
| Database | PostgreSQL 16 (ParadeDB), Valkey (Redis), Elasticsearch |
| Auth | JWT + SRP-6a + WebAuthn (Passkey) + TOTP 2FA |
| Package mgmt | uv (Python), pnpm (JS) |
| Task runner | [Taskfile](https://taskfile.dev/) |
| CI/CD | GitHub Actions, ghcr.io, Tailscale |

## Contributing

1. Create a feature branch from `main`
2. Make changes, run `task be:check` and `task fe:check`
3. Open a PR — Claude will auto-review
4. All PRs require review before merge; never commit directly to `main`
