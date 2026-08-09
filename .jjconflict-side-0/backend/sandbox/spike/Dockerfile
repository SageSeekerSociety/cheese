# Spike image: sandbox base + tmux + ttyd (static binary from GitHub — ttyd is
# not in debian's repo). Proves interactive claude-in-tmux is send-keys-drivable
# and hooks emit structured events in interactive mode.
FROM cheesex-agent-sandbox:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/*
RUN ARCH="$(uname -m)" && \
    curl -fsSL "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.${ARCH}" \
      -o /usr/local/bin/ttyd && chmod +x /usr/local/bin/ttyd && ttyd --version
USER node
