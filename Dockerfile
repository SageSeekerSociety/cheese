# ---- Base ----
FROM python:3.11-slim AS base

# Install system dependencies (including Rust toolchain for building srp_rs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    build-essential \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.cargo/bin:$PATH"

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# ---- Dependencies ----
FROM base AS deps
WORKDIR /app

# Copy dependency files and srp_rs source (needed for build)
COPY pyproject.toml uv.lock README.md ./
COPY srp_rs/ ./srp_rs/

# Install dependencies using uv (includes building srp_rs from source)
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

# Copy application code
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

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
