# syntax=docker/dockerfile:1
# Agent sandbox: 芝士 (claude code) runs INSIDE here, per topic, with native
# tools jailed to this container. The topic's git worktree is mounted in, and
# 芝士 commits to it itself.
FROM node:22-bookworm-slim

# Universal toolchain (the shared base, like Codex's `universal` image): package
# managers + build tools every project tends to need. Project-specific deps get
# installed by project environment scripts in the room's HOME and checkout.
# Only the package INDEX is cached, not the .deb archives: caching those means
# deleting the base image's docker-clean, and every image layered on this one —
# tmux.Dockerfile, and whatever a hosted repo builds — would silently start
# carrying its own downloaded .debs. This image is other people's base.
# `procps` and `openssh-client` are here for the same reason as the rest, and
# are the two that look droppable: a shell has `kill` as a BUILTIN and a slim
# image looks like it already has it, but anything that spawns the binary —
# `subprocess.run(["kill", ...])`, `pgrep`, `ps` — gets FileNotFoundError
# instead. `ssh-keygen` is the same shape: no shell fallback exists at all.
# Reuse the backend's HTTPS mirror; apt cannot reach Debian's CDN here.
# Node slim has no system CA bundle. Its bundled roots let apt verify HTTPS
# until ca-certificates installs the distribution's bundle in this same layer.
RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \
    mkdir -p /etc/ssl/certs && \
    node -p 'require("tls").rootCertificates.join("\n")' > /etc/ssl/certs/ca-certificates.crt && \
    sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates curl xz-utils python3 python3-pip \
      build-essential pkg-config ripgrep jq less unzip \
      postgresql-client libpq-dev \
      procps openssh-client \
      fonts-noto-cjk

# Fonts are here rather than beside the document tooling below because the way
# they go missing is silent: typst exits 0 and pandoc exits 0 on a machine with
# no CJK face, and every Chinese character in the PDF renders as an empty box.
# The file opens, its size is normal, and text extraction returns the correct
# characters — so nothing the platform can check catches it, only a person
# looking at the page. `fonts-noto-cjk` also carries several weights, which is
# what keeps bold from silently flattening to regular.

# git refuses to commit without an identity, so an image without one turns every
# commit — 芝士's own, and any a hosted repo's test suite makes — into a failure
# that names our environment rather than the work. `--system`, not `--global`:
# it has to hold for whatever user a derived image runs as and wherever HOME is
# mounted, and both are outside this file's control. Same identity the backend
# writes into the repos it creates, so a commit made in here and one made by the
# platform are by the same author; a repo that wants another one still wins,
# since repo-local and global config both outrank system.
RUN git config --system user.name "芝士" \
 && git config --system user.email "cheese@zhishi.local"

# uv — Python package/env manager (symmetric to npm). Installed from PyPI (the
# uv wheel bundles the binary) — reliable through the proxy, unlike ghcr. uv
# fetches its own Python (base OS python3 is 3.11, cheesex needs >=3.13 — uv
# resolves that regardless of the base).
ARG UV_VERSION=0.12.1
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --break-system-packages "uv==${UV_VERSION}" && uv --version

# GitHub CLI — so 芝士 can inspect CI/CD (`gh run view --log-failed`) with the
# read-only token `cheese gh-token` mints (#188/#193). curl works too, but gh
# is far less error-prone for reading Actions. Direct from releases (musl-free
# deb): reliable through the proxy, unlike ghcr/apt-key.
ARG GH_VERSION=2.62.0
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz" \
      | tar xz -C /tmp \
    && mv "/tmp/gh_${GH_VERSION}_linux_${ARCH}/bin/gh" /usr/local/bin/gh \
    && rm -rf "/tmp/gh_${GH_VERSION}_linux_${ARCH}" \
    && gh --version

# Document tooling. A room that produces a report, a paper or a set of slides
# needs these the way a code room needs a compiler, and neither can be installed
# from inside a room: the container runs as `node` and project setup scripts have
# no root, so anything that is not a single relocatable binary has to be here.
#
# pandoc READS what people upload. Without it an agent hand-parses WordprocessingML
# and gets it wrong in a way that looks like it worked — an empty `<w:t/>` makes a
# regex swallow the tags between two cells, and the garbage flows into the report.
# `pandoc x.docx -t markdown` is the whole problem, solved.
#
# typst WRITES PDFs. The alternatives both lose: LibreOffice wants a writable HOME
# profile, starts slowly and drags in Java for some filters, and headless Chromium
# needs ten system libraries this image does not carry (libnspr4, libnss3, libatk,
# libcups, libXcomposite, libatspi…), so it cannot be added from userland either.
# typst is one static binary with no system dependency, which is also why it is the
# only one of the three that behaves identically on an enrolled device.
ARG PANDOC_VERSION=3.7.0.2
ARG TYPST_VERSION=0.15.1
RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-${ARCH}.tar.gz" \
      | tar xz -C /tmp \
    && mv "/tmp/pandoc-${PANDOC_VERSION}/bin/pandoc" /usr/local/bin/pandoc \
    && rm -rf "/tmp/pandoc-${PANDOC_VERSION}" \
    && case "$ARCH" in amd64) TARCH=x86_64 ;; arm64) TARCH=aarch64 ;; *) echo "unsupported arch $ARCH" >&2; exit 1 ;; esac \
    && curl -fsSL "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-${TARCH}-unknown-linux-musl.tar.xz" \
      | tar xJ -C /tmp \
    && mv "/tmp/typst-${TARCH}-unknown-linux-musl/typst" /usr/local/bin/typst \
    && rm -rf "/tmp/typst-${TARCH}-unknown-linux-musl" \
    && pandoc --version | head -1 \
    && typst --version

# Claude Code CLI (the agent runtime that runs in-container). Pinned, and pinned
# to the SAME version app.domain.agent.device_launch.CLAUDE_PINNED_VERSION hands
# to enrolled devices — an unpinned install means a rebuild for any unrelated
# reason can change the agent runtime under a topic, and that a container and a
# device running "the same" turn are not running the same thing.
ARG CLAUDE_CODE_VERSION=2.1.261
RUN --mount=type=cache,target=/root/.npm \
    npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" && claude --version

WORKDIR /work

# Platform CLI (芝士 calls this for platform actions; files/code use native tools)
COPY cheese /usr/local/bin/cheese
RUN chmod +x /usr/local/bin/cheese && mkdir -p /work && chown -R node:node /work

# `node` is uid/gid 1000 here (node:22's own user) and the backend deliberately
# runs as the SAME uid — the two share one git store through the main-repo
# `.git` bind mount and both COMMIT into it, and git's object directories
# belong to whoever wrote them, so a uid split leaves one side unable to add
# anything. Do not usermod/groupmod this user; derived images keep `USER node`.
# The invariant is asserted by tests/unit/test_workspace_uid_alignment.py.
RUN test "$(id -u node)" = 1000 && test "$(id -g node)" = 1000
USER node
