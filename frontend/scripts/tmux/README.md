# Static `tmux` for the connector

The connector runs each agent's Claude Code inside a private `tmux`. To keep the
client prerequisite-free, the backend **serves a `tmux` binary** alongside `cheese`
(`install.sh` downloads it to `~/.config/cheese/bin/tmux`), so a target machine needs
nothing pre-installed. For that to work on *any* Linux, the served `tmux` must be
**fully static** — a distro tmux is dynamically linked against `libevent`, `ncurses`,
`libjemalloc`, … and would fail on a host missing them.

This directory builds that static binary. **Only the build recipe is in git — never the
binary** (`.connector-dist/` is git-ignored).

## Build

Requires Docker (with buildx, default on Docker ≥ 23). Reproducible, no host toolchain:

```bash
# linux-amd64 → $CONNECTOR_DIST_DIR/linux-amd64/tmux (default: <repo>/.connector-dist)
frontend/scripts/tmux/build-static-tmux.sh --arch amd64

# linux-arm64 (needs qemu binfmt: `docker run --privileged --rm tonistiigi/binfmt --install arm64`)
frontend/scripts/tmux/build-static-tmux.sh --arch arm64

# options
frontend/scripts/tmux/build-static-tmux.sh --arch amd64 --version 3.5a --out /path/to/dist
```

Verify:

```bash
file .connector-dist/linux-amd64/tmux   # → "statically linked"
ldd  .connector-dist/linux-amd64/tmux   # → "not a dynamic executable"
.connector-dist/linux-amd64/tmux -V     # → tmux 3.5a
```

## How it works

`Dockerfile` builds on Alpine (musl libc) with the `-static` archives of `libevent`
and `ncurses`, configures tmux with `--enable-static` + `-static -no-pie`, strips the
result, and stages it in a `scratch` layer. `build-static-tmux.sh` uses
`docker build --target export --output type=local` to copy just that binary onto the
host at `<out>/linux-<arch>/tmux`.

## Where it's served

`CONNECTOR_DIST_DIR` (see `backend/.env`; default `<repo>/.connector-dist`) is the root
the installer serves from: `GET <origin>/connector/latest/<os>-<arch>/tmux`
(`backend/app/api/routes/installer.py`). `install.sh` fetches
`$CONNECTOR_BASE/latest/$target/tmux`; if the origin publishes none it falls back to a
system tmux. So publishing a target's static tmux here is what removes the dependency
for that target. `cheese` itself is built by `frontend/scripts/build-connector.mjs`.

## macOS

macOS does not support fully static executables (no static libc). For darwin targets,
ship a tmux built against the system libraries, or rely on the user's `brew install tmux`
(the installer's system-tmux fallback). This script targets Linux only.
