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

# ---- Production ----
FROM base AS production

WORKDIR /app

# Copy dependencies from deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy application code (fusion unified the migration chain under alembic/)
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

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

# Create non-root user
RUN addgroup --system --gid 1001 python \
    && adduser --system --uid 1001 --gid 1001 cheese \
    && mkdir -p /app/uploads \
    && chown -R cheese:python /app

USER cheese

# Set PATH to use virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8081/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]