# cheesex-dev — the universal base + cheesex's OWN toolchain pre-installed, so 芝士
# can run and test cheesex itself inside the sandbox (本体自更新 / dogfooding on
# this repo). This is the "project layer" on top of the shared base: Claude Code
# web's "setup script + filesystem snapshot", expressed as a Dockerfile.
#
# BUILD CONTEXT = repo root (it copies backend/ and frontend/ manifests):
#   docker build -f backend/sandbox/cheesex-dev.Dockerfile -t cheesex-dev:v0 .
# Rebuild when backend/uv.lock or frontend/package-lock.json change (content-hash
# is the natural cache key — same idea as the spec's per-project snapshot).
FROM cheesex-agent-sandbox:latest

USER node
WORKDIR /home/node/prewarm

# Warm uv's cache with the backend deps (uv also fetches Python 3.13 here — the
# base OS python is 3.11). Copy only the manifests so this layer caches on lockfile
# content. The venv is throwaway; it exists only to populate ~/.cache/uv so that
# `uv sync` in the mounted /work worktree is fast (and largely offline).
COPY --chown=node:node backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/
RUN cd backend && uv sync --frozen --no-install-project && rm -rf .venv

# Warm npm's cache with the frontend deps (populates ~/.npm; node_modules is
# throwaway — the real install happens in /work).
COPY --chown=node:node frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci && rm -rf node_modules

WORKDIR /work
