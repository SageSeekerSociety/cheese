# cheese

`cheese` is a **generic terminal-hosting mechanism**. You install one small
binary on a machine, log it in to a server, and from then on the server can open
**screens** on that machine and drive them — with no further client updates,
ever. The CLI carries **zero** knowledge of what those screens are for; all the
meaning lives on the server and in the scripts the server sends down.

It is self-contained: this directory builds a single static binary that depends
on nothing but a system `tmux` and a POSIX pty.

## The model

A screen is a program running in a terminal. The server reaches each screen
three independent ways:

1. **The raw screen channel** — the server attaches to a screen's live byte
   stream and reads/writes it directly (the "现场"): full-fidelity terminal in,
   keystrokes and resizes out. This is what a browser terminal rides on.

2. **The screen's own socket** — a `prompt` call carries text the server wants
   the hosted program to receive as input, and the host hands it to the socket
   the program bound for itself. Nothing about that depends on the terminal:
   not pane width, not TUI state, not a screen scrape. A call that cannot be
   served is answered with an error, so the server never mistakes silence for
   delivery.

3. **File staging and one-shot exec** — bytes written into a screen's workspace
   before they are referred to, and commands run on the machine itself for
   setup and health checks.

The host binary is a dumb, safe sandbox that offers exactly these affordances
and ascribes meaning to none of it. Read the code in `internal/` and you cannot
tell what it is used for — that is the point.

## Commands

Two groups mirror the two ideas — who this machine is, and whether it is
connected:

```
cheese auth login [server-url]   log this machine in (device flow); first login names it
cheese auth logout               forget the credential (disconnects first)

cheese link connect [server-url] connect (runs login first if needed)
cheese link disconnect           disconnect (warns if screens are running)
cheese link auto-connect         connect now and reconnect on every boot
cheese link no-auto-connect      disconnect and stop reconnecting on boot

cheese status                    login, connection and screen count at a glance
cheese api <operation> [args]    the server's full request/response API
cheese update                    update the binary to the latest published build
cheese uninstall                 remove the CLI entirely (service, config, binary)
```

Commands meet the user where they are: `link connect` starts the login flow if
the machine isn't logged in yet; `link disconnect` warns (and asks) when live
screens would be killed; every success message says what to do next. The server
URL is remembered after the first login — installers can pre-seed it, after
which no command ever needs a URL.

**Login is a device flow.** `cheese auth login` registers the machine, prints a
link, and blocks until a human opens it and approves. On approval the server
hands back a durable credential representing this device (it does not expire —
revoke it server-side). The credential is stored at
`~/.config/cheese/config.json`; both the long-running host and `cheese api` read
it, so a machine is configured once. How the user authenticates on that link is
entirely the server's business.

**Self-contained tmux.** The host prefers a private tmux at
`~/.config/cheese/bin/tmux` (placed there by an installer; `$CHEESE_TMUX`
overrides) and only falls back to the system tmux — so a machine without tmux
works, and a machine with a quirky one is never at its mercy. Sockets live in a
private per-run directory either way.

**`cheese api`** mounts the server's OpenAPI (fetched live from
`<base>/openapi.json`) as subcommands, rebuilt each run so it never goes stale.
It is hidden from the top-level help; run `cheese api` to list operations and
`cheese api <op> -h` for one operation's generated help. Two things happen
automatically:

- the stored credential is attached as `Authorization: Bearer …`;
- if the call comes from **inside a screen**, it is tagged with that screen's
  token via `X-Cheese-Screen`. The host injects a per-screen token as
  `CHEESE_SCREEN` into every process a screen spawns, so the server can tell
  exactly which screen (and thus which hosted program) made any given call —
  with no cooperation from the program itself.

## Build

```bash
go build -o cheese .
# cross-compile (CGO-free) for the supported targets:
GOOS=linux  GOARCH=amd64 CGO_ENABLED=0 go build -o cheese-linux-amd64 .
GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 go build -o cheese-darwin-arm64 .
```

Runtime needs `tmux` and a pty (Linux and macOS; no Windows).
