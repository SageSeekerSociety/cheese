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

It runs in the foreground and, on shutdown, kills its private tmux session
(taking the agent with it). Keep it alive with `screen`, `tmux`, or systemd.

## Deploy on a client machine (persistent)

One-time setup — three things on the client:

1. the `cheesed` binary (build on the build machine, `scp` it over),
2. a `client.config.json` (see above — `backend_ws_url` points at our backend,
   `session_token` is the agent's minted token), and
3. `tmux` + the agent binary (`claude`) installed.

Then run cheesed in a **detached `screen`** so it survives your SSH logout:

```bash
screen -dmS cheesed ~/cheesed run --config ~/client.config.json
```

- `-dmS cheesed` starts it detached in a named session "cheesed".
- Reattach to watch its logs: `screen -r cheesed`  (detach again: Ctrl-a d).
- Check it's running: `screen -ls`.
- Stop it: `screen -XS cheesed quit`.

Verify it connected to the backend (one established TCP conn to the backend host):

```bash
ss -tnp | grep <backend-host>:<port>
```

**After a machine reboot**, screen sessions do not survive — just re-run the one
`screen -dmS …` line above. For true auto-start on boot, install a systemd user
unit instead:

```ini
# ~/.config/systemd/user/cheesed.service
[Unit]
Description=cheese connector daemon
After=network-online.target

[Service]
ExecStart=%h/cheesed run --config %h/client.config.json
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now cheesed
loginctl enable-linger $USER   # so it starts at boot without an active login
```

> Note: `session_token` is currently a demo stopgap (a fixed token the backend
> has seeded). Once `agent/session` login lands, the token is minted per agent
> and this config field is filled from that.
