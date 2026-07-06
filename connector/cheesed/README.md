# cheesed

The always-on agent daemon for the 知是 2.0 connector plane. It runs a Claude
Code session in a **private-socket tmux** on a client machine and relays that
terminal to the cheese backend over one dial-out WebSocket — screen frames go
up, controller input comes down. See `docs/design/architecture.md` §5.

## Thin by design

cheesed carries **zero business logic**. It has no prompt rules, no idle
detection, no local tool API, no "read-only" concept of its own. It is a
transparent read-write pipe. Everything that looks like policy lives in the
backend:

- **Who may type** ("逻辑只读" / takeover) is arbitrated entirely in the
  backend `SessionHub`, which simply does not forward a non-controller's input
  frames. cheesed never sees a takeover concept.
- **Driving the agent** (typing a prompt into Claude) is the backend sending
  input frames down the same channel a human controller uses — there is no
  separate "drive" path in the client.

Because no policy lives here, the backend can change how agents are driven
without ever reinstalling cheesed.

## What it does

1. Launches `agent.path` (e.g. `claude`) detached inside a private tmux session
   (socket under `$XDG_RUNTIME_DIR/cheesed/`, invisible to the operator's own
   `tmux ls`).
2. Dials `backend_ws_url` (with `session_token` in the `X-Cheese-Session`
   header), reconnecting with capped backoff, heart-beating while connected.
3. On demand — when the backend reports a viewer is present — attaches a pty to
   the tmux session and bridges it through the webtty protocol (xterm.js on the
   browser). The attach is torn down when the last viewer leaves; the tmux
   session itself keeps running.

Reliability is outsourced to mature libraries: `tmux` (session/scrollback),
`creack/pty` (pty), `gorilla/websocket` (transport), `sorenisanerd/gotty`
(webtty framing).

## Build

```bash
cd connector/cheesed
go build -o cheesed .
```

Both the build machine and the client are x86_64 linux, so build here and scp
the binary to the client. The client needs `tmux` and the agent binary
(`claude`) on it; cheesed itself is a single static-ish Go binary.

## Configure

Copy `example.config.json` and fill in the backend URL and session token (the
token is minted by the backend's `agent/session` login — never commit it):

```json
{
  "backend_ws_url": "ws://<backend-host>:8080/connector/agent",
  "session_token": "<minted-session-token>",
  "agent": { "path": "claude", "args": ["--permission-mode", "bypassPermissions"],
             "cwd": "/home/agent/workspace", "env": ["TERM=xterm-256color"] },
  "cols": 200, "rows": 50, "heartbeat_interval_ms": 15000
}
```

`tmux_path` is optional (resolved from PATH / next to the binary if empty).

## Run

```bash
./cheesed run --config config.json
```

It runs in the foreground; use a supervisor (systemd/tmux) to keep it alive.
On shutdown it kills its private tmux session.
