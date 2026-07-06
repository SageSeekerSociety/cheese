# web-claude

A complete, **isolated single-purpose** example built on the generic
[`cheese`](../../cli) CLI: a server + browser UI that hosts **Claude Code in the
browser**. You run `cheese` on some machine, and from a web page you open a
Claude session on it, watch and type into its terminal live, see status the
Claude cheeselet publishes, and — the interesting part — have processes *inside*
that Claude session call back into the server, correctly attributed to their
screen.

It is deliberately **not** the real Cheese backend. It implements only the slice
of the cheese protocol needed to prove the idea end to end, so it also serves as
a contract sample for the real backend. All the "this is about AI" knowledge
lives here and in the cheeselet; the `cheese` CLI itself knows none of it.

## What it demonstrates

```
 browser ──ws──┐                                   ┌── raw terminal (现场)
   xterm        │        web-claude server         │   tmux ⇄ claude
 dashboard ─────┤  (device auth · screens · api)   ├── cheeselet (claude.js)
               ws                 ▲   │              │   status/lastLine vars
                                  │   │ session.create│
                    cheese run ◀──┘   ▼   + CHEESE_SCREEN
                    on the device ─────────────────────┐
                                                        │  a process inside the
                                  cheese api whoami ────┘  screen calls back,
                                  (X-Cheese-Screen header)   tagged to its screen
```

- **Device login** — the device-flow that `cheese auth login` speaks
  (`/auth/device/start` → open link → approve → `/auth/device/poll`).
- **Screens** — the server opens a screen running `claude` with `claude.js` as
  its cheeselet, and injects a per-screen token.
- **Cheeselet variables** — `claude.js` reads Claude's screen and publishes
  `status` (busy/idle), `lastLine`, `lines`; the dashboard shows them live.
- **Raw 现场 relay** — the browser terminal is the screen's real byte stream.
- **Screen-scoped `cheese api`** — a process inside a screen that runs
  `cheese api whoami` / `cheese api post-note text: …` is automatically
  identified by its screen via the `X-Cheese-Screen` header; the dashboard shows
  each note attributed to the Claude that posted it.

## Run it

You need [uv](https://docs.astral.sh/uv/) for the server; the client machine
needs nothing pre-installed (`install.sh` takes care of it).

**1. Start the server** (from this directory):

```bash
uv run server/app.py                 # serves http://localhost:9100
# no Claude installed? drive any program instead:
SCREEN_PROGRAM=top uv run server/app.py
```

**2. Install cheese on the machine that will run Claude** (can be the same box):

```bash
./install.sh http://localhost:9100
```

This installs `cheese` to `~/.local/bin` (building it from `../../cli` if
needed), drops a private tmux at `~/.config/cheese/bin/tmux` so the machine
needs no tmux of its own, remembers the server URL, and prints what to do next
— which is:

```bash
cheese link connect        # runs the login flow (open the link, click Approve), then connects
```

**3. Open the dashboard** at <http://localhost:9100>. Your machine appears with
a green dot. Click **Open Claude screen**: a Claude session starts on the
machine and its terminal streams into the browser. The cheeselet reads Claude's
screen and publishes:

- `status` — starting / **busy** (spinner + "esc to interrupt") / **waiting**
  (a permission dialog needs a human decision) / **idle** / dead;
- `interaction` — the three-state gate (`none` / `scroll` / `full`) the viewer
  honors: while Claude is busy your keystrokes are *not* sent to the terminal;
- `activity` (the spinner verb), `lastReply` (the last `●` output block),
  `question` (the pending dialog when waiting).

The **chat panel** talks to the Claude in the active screen: your message is
typed into Claude (with instructions to answer back via
`cheese api post-note text: …`), and Claude's notes from that screen appear as
chat replies.

**4. See the callback chain.** Inside the Claude terminal (or ask Claude to run
it), execute:

```bash
cheese api whoami
cheese api post-note text: finished the refactor
```

`whoami` reports `inside_screen: true` and the screen id; the note appears in the
dashboard's **Notes** panel tagged with the screen that posted it. Neither the
shell nor Claude did anything special — `cheese` attached the screen token on its
own.

## Files

| Path                 | What it is                                                        |
|----------------------|-------------------------------------------------------------------|
| `install.sh`         | one-step client install: binary, private tmux, server URL, next steps |
| `server/app.py`      | the isolated server (device auth, ws `/agent`, screens, chat, `cheese api`, UI) |
| `cheeselet/claude.js`| the cheeselet that turns Claude's screen into status/interaction/reply variables |
| `web/index.html`     | the browser dashboard: xterm terminal (gated), variables, chat, notes |
| `pyproject.toml`     | uv project (FastAPI + uvicorn)                                    |

## Notes

- Everything is in-memory and single-process; restart clears all state.
- The `/approve` page is a bare button standing in for a real human login — the
  point is the *shape* of the device flow, not the identity check.
- The durable device credential does not expire; a real backend would let a user
  list and revoke devices.
