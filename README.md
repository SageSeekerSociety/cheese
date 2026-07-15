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

## Quick Start

```bash
# 1. Clone
git clone https://github.com/SageSeekerSociety/cheese-backend-py.git
cd cheese-backend-py

# 2. Configure environment
cp backend/.env.example backend/.env    # edit if needed

# 3. One-command setup (install deps + start infra + run migrations)
task setup

# 4. Start development (backend + frontend in parallel)
task dev        # backend on localhost:8081, frontend on localhost:3000
```

Open http://localhost:3000 in your browser.

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
├── docker-compose.yml       # dev infrastructure (PG, Valkey)
├── docker-compose.dev.yml   # optional: backend in Docker (Windows without WSL)
└── Taskfile.yml             # unified task runner
```

## Common Commands

```bash
task                         # show all available commands
task setup                   # first-time setup (install deps + infra + migrations)
task dev                     # start backend + frontend in parallel
task check                   # run all checks (backend + frontend)

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
task infra                   # start PG/Valkey
task infra:down              # stop all
task infra:logs              # tail logs

# E2E
task e2e:test                # run Playwright tests
task e2e:test:headed         # with visible browser
```

## Development Environments

### Linux / macOS / WSL (recommended)

Everything runs natively — `uv run` for backend, `pnpm` for frontend, Docker for infrastructure only.

### Windows

Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) (`wsl --install`, reboot), then work inside WSL — same as Linux.

Without WSL:

```bash
# First-time: start infrastructure + backend in Docker
task dev:docker              # PG, Valkey + backend on localhost:8081

# Frontend runs natively (install Node + pnpm first)
cd frontend && pnpm install
task dev:fe                  # Vite dev server on localhost:3000

# Run backend checks inside the container
docker compose exec backend sh -c "uv run ruff check . && uv run pyright"
docker compose exec backend sh -c "uv run pytest tests/ -n 8 -q"
```

## Deployment

```bash
# When ready to deploy (after merging PRs to main):
gh release create v2026.05.1 --generate-notes
# → builds Docker images → reviewer approves → deploys via Tailscale SSH
```

Release-based deployment with approval gate. See [`deploy/`](deploy/) for production Docker Compose, environment template, and deploy script.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.x (async), Pydantic v2 |
| Frontend | Vue 3, TypeScript, Vuetify, Vite |
| Database | PostgreSQL 16 (ParadeDB), Valkey (Redis); search via Meilisearch (optional, PG FTS fallback) |
| Auth | JWT + SRP-6a + WebAuthn (Passkey) + TOTP 2FA |
| Package mgmt | uv (Python), pnpm (JS) |
| Task runner | [Taskfile](https://taskfile.dev/) |
| CI/CD | GitHub Actions, ghcr.io, Tailscale |

## Contributing

1. Create a feature branch from `main`
2. Make changes, run `task check`
3. Open a PR — Claude will auto-review
4. All PRs require review before merge; never commit directly to `main`
