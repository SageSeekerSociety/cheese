# Agent sandbox: 芝士 (claude code) runs INSIDE here, per topic, with native
# tools jailed to this container. jj (colocated git) auto-snapshots edits.
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates curl xz-utils python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# jujutsu (jj) — auto-snapshotting VCS, git-compatible
ARG JJ_VERSION=0.42.0
RUN curl -fsSL "https://github.com/jj-vcs/jj/releases/download/v${JJ_VERSION}/jj-v${JJ_VERSION}-aarch64-unknown-linux-musl.tar.gz" \
      | tar xz -C /usr/local/bin ./jj \
    && jj --version

# Claude Code CLI (the agent runtime that runs in-container)
RUN npm install -g @anthropic-ai/claude-code && claude --version

WORKDIR /work

# Platform CLI (芝士 calls this for platform actions; files/code use native tools)
COPY cheese /usr/local/bin/cheese
RUN chmod +x /usr/local/bin/cheese && mkdir -p /work && chown -R node:node /work
USER node
