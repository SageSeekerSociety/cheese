# syntax=docker/dockerfile:1

# ---- Base ----
FROM python:3.11-slim AS base

# ① 更换 apt 源为清华镜像（Debian 新格式支持 deb822）
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources

# ② 使用 BuildKit 缓存挂载，即使本层失败，下载的包也会保留
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        git \
        postgresql-client \
        build-essential \
        # The backend SSHes into machines it provisions to enroll them as agent
        # hosts; without a client that whole path is dead in the image.
        openssh-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 安装 Rust 工具链（如果下载慢可加国内镜像，这里保留原样）
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:$PATH"

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Node.js (required by pyright) in a separate layer to keep memory low
RUN apt-get update && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Dependencies ----
FROM base AS deps
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
# production stage is `FROM base` (system python 3.11 only) and copies just the
# venv, so that symlink would dangle -> `exec .../uvicorn: no such file or
# directory` at startup. Install the managed interpreter to a fixed, copyable
# path so production can COPY it too. (UV_LINK_MODE=copy does NOT fix this — it
# only affects package files, not the interpreter symlink.)
ENV UV_PYTHON_INSTALL_DIR=/opt/python

# Install dependencies using uv (includes building srp_rs from source)
# 如果有额外下载慢的依赖，也可以给 uv 加镜像源：--index-url 或配置 .netrc
RUN uv sync --frozen --no-dev

# ---- Development ----
FROM deps AS development
WORKDIR /app

# Install dev dependencies
RUN uv sync --frozen

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
RUN set -eu; for target in darwin-arm64 darwin-amd64 linux-arm64 linux-amd64; do \
      os="${target%-*}"; arch="${target#*-}"; \
      GOFLAGS=-mod=mod GOOS="$os" GOARCH="$arch" \
        go build -buildvcs=false -ldflags "-s -w -X main.version=cheesex-connector" \
        -o "/out/$target/cheesehost" . ; \
      echo "built $target/cheesehost"; \
    done

# ---- Production ----
FROM base AS production

WORKDIR /app

# Copy dependencies from deps stage — both the venv AND the managed interpreter
# it symlinks to (see UV_PYTHON_INSTALL_DIR in the deps stage), or the venv's
# python is a dangling symlink and uvicorn can't start.
COPY --from=deps /opt/python /opt/python
COPY --from=deps /app/.venv /app/.venv

# Copy application code (fusion unified the migration chain under alembic/)
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# One-shot data migration an operator has to be able to run ON the box (memory
# rows → openviking, #187). Named file by file rather than `COPY scripts`: the
# rest of that directory is dev probes and demo seeders that have no business
# in a production image.
COPY scripts/migrate_memory_to_openviking.py ./scripts/
# The agent-side assets the BACKEND serves at runtime: the `cheese` platform CLI
# (fetched by enrolled devices via /sandbox/cli/cheese) and the skills seeded
# into a topic's ~/.claude. Without these the device path has no CLI.
COPY sandbox ./sandbox
# Served at /connector/latest/<os>-<arch>/cheesehost (see the connector stage).
COPY --from=connector /out ./connector-dist
# Verify at the source layer rather than pulling/running the multi-GB image after
# it was pushed. A missing COPY still fails the gated build, but without the
# former three-minute registry round trip.
RUN set -eu; for target in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64; do \
      f="/app/connector-dist/$target/cheesehost"; \
      test -s "$f"; \
      echo "verified $target ($(wc -c < "$f") bytes)"; \
    done

# Agent workspace toolchain: worktrees are managed with jj (colocated git) by
# the backend process itself, and sandboxed turns spawn SIBLING containers
# through the host's docker.sock — so the image needs the docker CLI (client
# only, no daemon). Same jj install as backend/sandbox/Dockerfile.
ARG JJ_VERSION=0.42.0
RUN curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-$(uname -m)-unknown-linux-musl.tar.gz" \
      | tar xz -C /usr/local/bin ./jj \
    && jj --version
ARG DOCKER_CLI_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && docker --version

# Create non-root user.
#
# The uid/gid are NOT free to pick: this process and the agent sandbox share one
# jj store on disk (ws.sandbox_vcs_mounts bind-mounts the project's main-repo
# .jj/.git into every sandbox container, read-write). jj writes its store objects
# — and `.jj/repo/config-id` in particular — with a hardcoded 0600, so whichever
# uid touches the store first locks the other one out of EVERY jj command
# ("Failed to determine the secure config for a repo … Permission denied").
# Backend-side that surfaced as a project-wide 422 on the file panel; sandbox-side
# as jj being unusable in every container. umask/group/ACL cannot fix a mode the
# writer sets explicitly, so the two identities have to BE the same identity.
#
# 1000 rather than some other number because the sandbox side is the one we do
# not fully control: it is `node:22` + `USER node` (= 1000), the tmux provider
# starts it with `--user node`, and a project may point `sandbox_image` at any
# other node-based image. See backend/sandbox/Dockerfile and
# app.domain.workspace.service.AGENT_UID (which tests pin against this line).
RUN addgroup --system --gid 1000 python \
    && adduser --system --uid 1000 --gid 1000 cheese \
    && test "$(id -u cheese)" = 1000 \
    && test "$(id -g cheese)" = 1000 \
    && mkdir -p /app/uploads \
    && chown -R cheese:python /app

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
