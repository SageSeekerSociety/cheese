# Agent sandbox: 芝士 (claude code) runs INSIDE here, per topic, with native
# tools jailed to this container. jj (colocated git) auto-snapshots edits.
FROM node:22-bookworm-slim

# Universal toolchain (the shared base, like Codex's `universal` image): package
# managers + build tools every project tends to need. Project-specific deps get
# layered on top (uv sync / npm ci) — see cheesex-dev.Dockerfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates curl xz-utils python3 python3-pip \
      build-essential pkg-config ripgrep jq less unzip \
      postgresql-client libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# uv — Python package/env manager (symmetric to npm). Installed from PyPI (the
# uv wheel bundles the binary) — reliable through the proxy, unlike ghcr. uv
# fetches its own Python (base OS python3 is 3.11, cheesex needs >=3.13 — uv
# resolves that regardless of the base).
RUN pip3 install --no-cache-dir --break-system-packages uv && uv --version

# jujutsu (jj) — auto-snapshotting VCS, git-compatible
ARG JJ_VERSION=0.42.0
RUN curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-$(uname -m)-unknown-linux-musl.tar.gz" \
      | tar xz -C /usr/local/bin ./jj \
    && jj --version

# GitHub CLI — so 芝士 can inspect CI/CD (`gh run view --log-failed`) with the
# read-only token `cheese gh-token` mints (#188/#193). curl works too, but gh
# is far less error-prone for reading Actions. Direct from releases (musl-free
# deb), same reason as jj: reliable through the proxy, unlike ghcr/apt-key.
ARG GH_VERSION=2.62.0
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz" \
      | tar xz -C /tmp \
    && mv "/tmp/gh_${GH_VERSION}_linux_${ARCH}/bin/gh" /usr/local/bin/gh \
    && rm -rf "/tmp/gh_${GH_VERSION}_linux_${ARCH}" \
    && gh --version

# Claude Code CLI (the agent runtime that runs in-container)
RUN npm install -g @anthropic-ai/claude-code && claude --version

WORKDIR /work

# Platform CLI (芝士 calls this for platform actions; files/code use native tools)
COPY cheese /usr/local/bin/cheese
RUN chmod +x /usr/local/bin/cheese && mkdir -p /work && chown -R node:node /work
USER node
