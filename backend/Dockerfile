# syntax=docker/dockerfile:1

# ---- Runtime base ----
# Only what the SERVER needs at runtime. The compilers live in build-base below:
# `production` starts from this stage, so anything added here ships to prod.
FROM python:3.11-slim AS runtime-base

# ① 更换 apt 源为清华镜像（Debian 新格式支持 deb822）
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources

# ② 用 BuildKit 缓存挂载存 apt 的下载与索引。base image 自带的 docker-clean
# 会在每次 install 后删掉刚下载的 .deb，正好抵消 /var/cache/apt 这个挂载，所以
# 先把它删掉；两个目录都是挂载，不进 layer，因此也不需要再 apt-get clean。
RUN rm -f /etc/apt/apt.conf.d/docker-clean
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        # The backend shells out to git for every workspace, and serves the
        # smart-HTTP endpoints from git's own exec-path.
        git \
        postgresql-client \
        # The backend SSHes into machines it provisions to enroll them as agent
        # hosts; without a client that whole path is dead in the image.
        openssh-client

# Create the non-root user HERE, before anything is copied, so every later COPY
# can set its owner as it writes. A `chown -R` over a populated /app instead
# copies up every file it touches into a fresh layer — measured at 101s and
# roughly a duplicate of .venv + connector-dist on 2026-08-17.
#
# The uid/gid are NOT free to pick: this process and the agent sandbox share one
# git store on disk (ws.sandbox_vcs_mounts bind-mounts the project's main-repo
# `.git` into every sandbox container, read-write) and BOTH commit into it — the
# agent's own commit is how a topic branch moves. git creates object directories
# 0755 and loose objects 0444, owned by whoever wrote them, so a second uid can
# read every one of them and add nothing: its commit fails on a directory it
# does not own. Backend-side that surfaced as a project-wide 422 on the file
# panel; sandbox-side as an agent whose work could not leave the container.
#
# 1000 rather than some other number because the sandbox side is the one we do
# not fully control: it is `node:22` + `USER node` (= 1000), the tmux provider
# starts it with `--user node`, and a project may point `sandbox_image` at any
# other node-based image. See backend/sandbox/Dockerfile and
# app.domain.workspace.service.AGENT_UID (which tests pin against this line).
RUN addgroup --system --gid 1000 python \
    && adduser --system --uid 1000 --gid 1000 cheese \
    && test "$(id -u cheese)" = 1000 \
    && test "$(id -g cheese)" = 1000

WORKDIR /app

# ---- Build toolchain ----
# Compilers and package managers, needed to BUILD the venv (srp_rs is a pyo3
# extension compiled from source) and never after. Kept out of runtime-base so
# production does not carry ~1.5GB of rustup + build-essential it cannot use.
FROM runtime-base AS build-base

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends build-essential

# 安装 Rust 工具链（如果下载慢可加国内镜像，这里保留原样）
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:$PATH"

# Install uv for fast Python package management. Pinned to the version the CI
# workflows install (astral-sh/setup-uv `version:`), so a resolver change cannot
# reach the image without also reaching the test jobs — and so `latest` cannot
# silently invalidate every layer below this one.
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

# ---- Dependencies ----
FROM build-base AS deps
WORKDIR /app

# Copy dependency files and srp_rs source (needed for build). `.python-version`
# is REQUIRED here: pyproject requires-python is ">=3.13", so without the pin uv
# fetches the newest satisfying interpreter — now Python 3.14, which pyo3 0.24
# (srp_rs) refuses to build against. The pin keeps uv on 3.13.
COPY pyproject.toml uv.lock README.md .python-version ./
COPY srp_rs/ ./srp_rs/

# Belt-and-braces: even if .python-version is ever dropped, hold uv on 3.13 so
# the srp_rs / pyo3 native build can't silently break on a newer interpreter.
ENV UV_PYTHON=3.13

# uv downloads a managed CPython and the venv's bin/python SYMLINKS to it. The
# production stage is `FROM runtime-base` (system python 3.11 only) and copies
# just the venv, so that symlink would dangle -> `exec .../uvicorn: no such file
# or directory` at startup. Install the managed interpreter to a fixed, copyable
# path so production can COPY it too. (UV_LINK_MODE=copy does NOT fix this — it
# only affects package files, not the interpreter symlink.)
ENV UV_PYTHON_INSTALL_DIR=/opt/python

# Install dependencies using uv (includes building srp_rs from source).
# The cache mounts keep uv's wheel downloads and Cargo's registry/build output
# across builds, so a lockfile edit recompiles only what changed. Cargo's mount
# points at the sub-directories, NOT at /root/.cargo — rustup put the `cargo`
# binary there and a mount over it would hide the toolchain from this very step.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/root/.cargo/git \
    --mount=type=cache,target=/app/srp_rs/target,sharing=locked \
    uv sync --frozen --no-dev

# ---- Development ----
FROM deps AS development
WORKDIR /app

# Node.js is pyright's runtime; pyright is a dev dependency, so this belongs to
# the dev image alone.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends nodejs

# Install dev dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/root/.cargo/git \
    --mount=type=cache,target=/app/srp_rs/target,sharing=locked \
    uv sync --frozen

# Copy source code
COPY . .

EXPOSE 8081

# Default command for development
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081", "--reload"]

# ---- Connector ----
# The `cheesehost` binaries the backend serves at /connector/latest/<target>, so
# a machine joins with one line (`curl .../connector/install.sh | sh`). Without
# them that endpoint 404s and the whole self-hosting path is dead on arrival.
#
# Their source is the repo-root `cli/`, which sits OUTSIDE this image's build
# context (./backend) — hence the named `cli` context, passed by build.yml. Only
# the production target needs it; `target: development` never builds this stage,
# so the dev compose flow is unaffected. To build production by hand:
#   docker build --build-context cli=./cli -f backend/Dockerfile \
#     --target production ./backend
FROM golang:1.26-bookworm AS connector
WORKDIR /src
COPY --from=cli . .
# Same targets and ldflags as backend/scripts/build-connector.sh, which stays
# the way to produce these for a local (non-Docker) backend.
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    set -eu; for target in darwin-arm64 darwin-amd64 linux-arm64 linux-amd64; do \
      os="${target%-*}"; arch="${target#*-}"; \
      GOFLAGS=-mod=mod GOOS="$os" GOARCH="$arch" \
        go build -buildvcs=false -ldflags "-s -w -X main.version=cheesex-connector" \
        -o "/out/$target/cheesehost" . ; \
      echo "built $target/cheesehost"; \
    done

# ---- Production ----
FROM runtime-base AS production

WORKDIR /app

# Everything below is ordered by how often it changes, rarest first: a plain
# code commit must invalidate only the last COPY. Put anything new in the band
# that matches its churn — a 150MB connector-dist rebuilt on every commit is
# 150MB re-exported and re-pushed on every commit.

# Agent workspace toolchain: sandboxed turns spawn SIBLING containers through
# the host's docker.sock, so the image needs the docker CLI (client only, no
# daemon).
ARG DOCKER_CLI_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && docker --version

# Copy dependencies from deps stage — both the venv AND the managed interpreter
# it symlinks to (see UV_PYTHON_INSTALL_DIR in the deps stage), or the venv's
# python is a dangling symlink and uvicorn can't start.
COPY --from=deps /opt/python /opt/python
COPY --from=deps --chown=cheese:python /app/.venv /app/.venv

# Served at /connector/latest/<os>-<arch>/cheesehost (see the connector stage).
COPY --from=connector --chown=cheese:python /out ./connector-dist
# Verify at the source layer rather than pulling/running the multi-GB image after
# it was pushed. A missing COPY still fails the gated build, but without the
# former three-minute registry round trip.
RUN set -eu; for target in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64; do \
      f="/app/connector-dist/$target/cheesehost"; \
      test -s "$f"; \
      echo "verified $target ($(wc -c < "$f") bytes)"; \
    done

# Application code (fusion unified the migration chain under alembic/). Last,
# because this is what every commit touches.
COPY --chown=cheese:python app ./app
COPY --chown=cheese:python alembic ./alembic
COPY --chown=cheese:python alembic.ini ./
# The agent-side assets the BACKEND serves at runtime: the `cheese` platform CLI
# (fetched by enrolled devices via /sandbox/cli/cheese) and the skills seeded
# into a topic's ~/.claude. Without these the device path has no CLI.
COPY --chown=cheese:python sandbox ./sandbox
# One-shot data migration an operator has to be able to run ON the box (memory
# rows to openviking, #187). Named file by file rather than `COPY scripts`: the
# rest of that directory is dev probes and demo seeders that have no business in
# a production image.
COPY --chown=cheese:python scripts/migrate_memory_to_openviking.py ./scripts/

# WORKDIR created /app as root; the app writes into it (and into uploads).
RUN mkdir -p /app/uploads && chown cheese:python /app /app/uploads

USER cheese

# Set PATH to use virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# The commit this image was built from, so the running app can report its own
# version (settings.app_version → /api/version). Passed by build.yml; "dev" for
# a plain local build.
ARG GIT_SHA=dev
ENV APP_VERSION=$GIT_SHA

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8081/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
