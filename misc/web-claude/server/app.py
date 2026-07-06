"""web-claude — an isolated, single-purpose server that hosts Claude Code in the
browser on top of the generic `cheese` CLI.

It is deliberately NOT the real Cheese backend: it implements just the slice of
the cheese protocol needed to prove the whole idea end to end, so it doubles as
the contract the real backend must satisfy. What it does:

  * device login (the device flow `cheese auth login` speaks),
  * accepts a `cheese` control connection at ws /agent,
  * opens "screens" on a connected device, each running a command (here: claude)
    with a hosted cheeselet, and injects a per-screen token,
  * relays a screen's raw terminal to/from a browser (xterm) at ws /ui/screen,
  * shows the cheeselet's variables live, and
  * exposes a tiny request/response API (whoami, notes) that a process INSIDE a
    screen reaches with `cheese api …`; every such call arrives tagged with the
    screen's token via the X-Cheese-Screen header, so the server knows exactly
    which screen (hence which Claude) made it.

The `cheese` CLI itself knows none of this — it only sees terminals, variables,
functions and raw bytes. All the meaning lives here and in the cheeselet.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CHEESELET = (ROOT / "cheeselet" / "claude.js").read_text()
INDEX_HTML = (ROOT / "web" / "index.html").read_text()

# The program each new screen runs (default: claude). It is launched through a
# login shell — `bash -lc 'exec <program>'` — so it is found on the user's real
# PATH (~/.local/bin, nvm, etc.) even when `cheese` runs as a systemd --user
# service with a stripped-down PATH. Override the program with SCREEN_PROGRAM,
# e.g. SCREEN_PROGRAM=top when you don't have claude installed.
SCREEN_PROGRAM = os.environ.get("SCREEN_PROGRAM", "claude")
SCREEN_COMMAND = ["bash", "-lc", f"exec {SCREEN_PROGRAM}"]

# The cheese wire-protocol version this server speaks. It is announced to a
# connecting device (`welcome`) and read back from the device's `hello`, so a
# mismatch is visible and future revisions can negotiate.
PROTOCOL_VERSION = 1

# Claude stores trust per directory in ~/.claude.json; an untrusted cwd makes it
# open a "Do you trust this folder?" gate at startup. Before opening a claude
# screen we run this on the device (via the generic exec RPC) to mark the screen's
# cwd trusted, so the gate never appears — deterministic, no keystroke racing.
# (The cheeselet's auto-trust remains a fallback.) As an argv it needs no shell
# quoting; it prints the cwd it trusted, which doubles as an exec smoke test.
PRETRUST_PY = (
    "import json,os\n"
    "p=os.path.expanduser('~/.claude.json')\n"
    "try:\n d=json.load(open(p))\n"
    "except Exception:\n d={}\n"
    "proj=d.setdefault('projects',{})\n"
    "proj.setdefault(os.getcwd(),{})['hasTrustDialogAccepted']=True\n"
    "json.dump(d,open(p,'w'))\n"
    "print(os.getcwd())\n"
)


# --------------------------------------------------------------------------- #
# In-memory state (single process, no database — this is a demo server).
# --------------------------------------------------------------------------- #


class Screen:
    def __init__(self, sid: str, device_id: str, command: list[str]):
        self.sid = sid
        self.device_id = device_id
        self.command = command
        self.token = uuid.uuid4().hex  # opaque per-screen token (the CHEESE_SCREEN value)
        self.vars: dict[str, Any] = {}
        self.chat: list[dict[str, str]] = []  # [{role: user|claude, text}]
        self.viewers: set[WebSocket] = set()  # browser terminals attached to this screen


class Device:
    def __init__(self, device_id: str, name: str, token: str):
        self.id = device_id
        self.name = name
        self.token = token
        self.ws: WebSocket | None = None
        self.proto: int | None = None  # protocol version the device announced
        self.send_lock = asyncio.Lock()
        self.screens: dict[str, Screen] = {}
        self.call_seq = 0  # ids for server->cheeselet calls (results ignored here)
        self.exec_seq = 0
        self.exec_pending: dict[str, asyncio.Future] = {}

    async def send(self, msg: dict[str, Any]) -> None:
        async with self.send_lock:
            if self.ws is not None:
                await self.ws.send_json(msg)

    async def exec(self, argv: list[str], cwd: str | None = None,
                   env: dict[str, str] | None = None, timeout: float = 60,
                   stdin: str | None = None) -> dict[str, Any]:
        """Run a one-shot command on the device and return {stdout, stderr, exit,
        truncated}. A generic capability (device setup, health checks, …) — not a
        screen. `timeout` is passed to the device so it bounds the command itself;
        if even that plus a margin elapses we tell the device to cancel it."""
        self.exec_seq += 1
        eid = f"e{self.exec_seq}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self.exec_pending[eid] = fut
        msg: dict[str, Any] = {"t": "exec", "id": eid, "command": argv, "timeout": int(timeout)}
        if cwd:
            msg["cwd"] = cwd
        if env:
            msg["env"] = env
        if stdin:
            msg["stdin"] = stdin
        await self.send(msg)
        try:
            return await asyncio.wait_for(fut, timeout=timeout + 5)
        except asyncio.TimeoutError:
            await self.send({"t": "exec.cancel", "id": eid})
            raise
        finally:
            self.exec_pending.pop(eid, None)


DEVICES: dict[str, Device] = {}          # device_id -> Device
BY_TOKEN: dict[str, Device] = {}         # device token -> Device
BY_SCREEN_TOKEN: dict[str, Screen] = {}  # screen token -> Screen
AUTH_CODES: dict[str, dict[str, Any]] = {}
NOTES: list[dict[str, Any]] = []
UI_EVENTS: set[WebSocket] = set()        # browser event sockets (dashboard updates)


async def broadcast_ui(event: dict[str, Any]) -> None:
    dead = []
    for ws in list(UI_EVENTS):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        UI_EVENTS.discard(ws)


def dashboard_state() -> dict[str, Any]:
    return {
        "type": "state",
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "online": d.ws is not None,
                "screens": [
                    {"sid": s.sid, "command": " ".join(s.command), "vars": s.vars,
                     "chat": s.chat[-50:]}
                    for s in d.screens.values()
                ],
            }
            for d in DEVICES.values()
        ],
        "notes": NOTES[-50:],
    }


app = FastAPI(title="web-claude", version="0.1.0")


# --------------------------------------------------------------------------- #
# Device login flow (what `cheese auth login` drives).
# --------------------------------------------------------------------------- #


class DeviceStart(BaseModel):
    device_name: str | None = None


class DevicePoll(BaseModel):
    device_code: str


@app.post("/auth/device/start", include_in_schema=False)
async def device_start(body: DeviceStart, request: Request) -> dict[str, Any]:
    code = uuid.uuid4().hex
    AUTH_CODES[code] = {
        "device_name": body.device_name or "unnamed",
        "status": "pending",
        "token": None,
        "device_id": None,
    }
    approve_url = str(request.base_url).rstrip("/") + f"/approve?code={code}"
    return {"device_code": code, "approve_url": approve_url, "interval": 1}


@app.post("/auth/device/poll", include_in_schema=False)
async def device_poll(body: DevicePoll) -> dict[str, Any]:
    entry = AUTH_CODES.get(body.device_code)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown device_code")
    if entry["status"] != "approved":
        return {"status": entry["status"]}
    return {
        "status": "approved",
        "token": entry["token"],
        "device_id": entry["device_id"],
        "device_name": entry["device_name"],
    }


@app.get("/approve", include_in_schema=False)
async def approve_page(code: str) -> HTMLResponse:
    entry = AUTH_CODES.get(code)
    if entry is None:
        return HTMLResponse("<h1>Unknown or expired link.</h1>", status_code=404)
    # In the real backend this page lives behind a human login; here it is a bare
    # button — the point is the shape of the flow, not the identity check.
    body = f"""
    <html><head><meta charset="utf-8"><title>Approve device</title>
    <style>body{{font-family:system-ui;max-width:32rem;margin:4rem auto;line-height:1.6}}
    button{{font-size:1.1rem;padding:.6rem 1.4rem;border-radius:.5rem;border:0;background:#6b46c1;color:#fff;cursor:pointer}}</style>
    </head><body>
    <h1>Approve this machine?</h1>
    <p>A machine calling itself <b>{entry['device_name']}</b> wants to connect.</p>
    <form method="post" action="/approve">
      <input type="hidden" name="code" value="{code}">
      <button type="submit">Approve</button>
    </form>
    </body></html>
    """
    return HTMLResponse(body)


@app.post("/approve", include_in_schema=False)
async def approve_submit(code: str = Form(...)) -> HTMLResponse:
    entry = AUTH_CODES.get(code)
    if entry is None:
        return HTMLResponse("<h1>Unknown or expired link.</h1>", status_code=404)
    if entry["status"] != "approved":
        device_id = uuid.uuid4().hex[:12]
        token = secrets.token_urlsafe(24)
        device = Device(device_id, entry["device_name"], token)
        DEVICES[device_id] = device
        BY_TOKEN[token] = device
        entry.update(status="approved", token=token, device_id=device_id)
        await broadcast_ui(dashboard_state())
    return HTMLResponse("<h1>Approved ✓</h1><p>You can return to the terminal.</p>")


class RenameBody(BaseModel):
    device_name: str


def device_from_auth(authorization: str | None) -> Device:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    device = BY_TOKEN.get(token)
    if device is None:
        raise HTTPException(status_code=401, detail="unknown token")
    return device


@app.post("/auth/device/rename", include_in_schema=False)
async def device_rename(
    body: RenameBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    device = device_from_auth(authorization)
    device.name = body.device_name
    await broadcast_ui(dashboard_state())
    return {"ok": True}


# --------------------------------------------------------------------------- #
# The screen-scoped request/response API — this is what `cheese api …` calls.
# Only these operations appear in the OpenAPI (everything else is
# include_in_schema=False), so `cheese api` shows a clean, tiny surface.
# --------------------------------------------------------------------------- #


def screen_from_headers(
    authorization: str | None, x_cheese_screen: str | None
) -> tuple[Device, Screen | None]:
    device = device_from_auth(authorization)
    screen = BY_SCREEN_TOKEN.get(x_cheese_screen or "")
    return device, screen


@app.get("/api/whoami", operation_id="whoami")
async def whoami(
    authorization: str | None = Header(default=None),
    x_cheese_screen: str | None = Header(default=None),
) -> dict[str, Any]:
    """Report who is calling: which device, and which screen (if any)."""
    device, screen = screen_from_headers(authorization, x_cheese_screen)
    return {
        "device_id": device.id,
        "device_name": device.name,
        "screen": screen.sid if screen else None,
        "inside_screen": screen is not None,
    }


class NoteBody(BaseModel):
    text: str


@app.post("/api/notes", operation_id="post-note")
async def post_note(
    body: NoteBody,
    authorization: str | None = Header(default=None),
    x_cheese_screen: str | None = Header(default=None),
) -> dict[str, Any]:
    """Post a note. If called from inside a screen it is attributed to it."""
    device, screen = screen_from_headers(authorization, x_cheese_screen)
    note = {
        "text": body.text,
        "device": device.name,
        "screen": screen.sid if screen else None,
    }
    NOTES.append(note)
    if screen is not None:  # a note from inside a screen is that Claude's chat reply
        screen.chat.append({"role": "claude", "text": body.text})
    await broadcast_ui(dashboard_state())
    return {"ok": True, "note": note}


@app.get("/api/notes", operation_id="list-notes")
async def list_notes(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """List posted notes."""
    device_from_auth(authorization)
    return {"notes": NOTES}


# --------------------------------------------------------------------------- #
# Browser-facing UI: static page, dashboard events, open-a-screen, terminal.
# --------------------------------------------------------------------------- #


@app.get("/", include_in_schema=False)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.websocket("/ui/events")
async def ui_events(ws: WebSocket) -> None:
    await ws.accept()
    UI_EVENTS.add(ws)
    await ws.send_json(dashboard_state())
    try:
        while True:
            await ws.receive_text()  # client sends nothing; keep the socket open
    except WebSocketDisconnect:
        pass
    finally:
        UI_EVENTS.discard(ws)


class ExecBody(BaseModel):
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: float = 60
    stdin: str | None = None


@app.post("/ui/devices/{device_id}/exec", include_in_schema=False)
async def ui_exec(device_id: str, body: ExecBody) -> dict[str, Any]:
    """Run a one-shot command on the device and return its output — the generic
    remote-exec primitive (no interactive screen needed)."""
    device = DEVICES.get(device_id)
    if device is None or device.ws is None:
        raise HTTPException(status_code=404, detail="device not connected")
    try:
        return await device.exec(body.argv, cwd=body.cwd, env=body.env,
                                 timeout=body.timeout, stdin=body.stdin)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="exec timed out")


@app.post("/ui/devices/{device_id}/open", include_in_schema=False)
async def ui_open_screen(device_id: str) -> JSONResponse:
    device = DEVICES.get(device_id)
    if device is None or device.ws is None:
        raise HTTPException(status_code=404, detail="device not connected")
    # For claude, pre-trust the device's cwd via the generic exec RPC so the
    # "Do you trust this folder?" gate never appears. This is also our live use
    # (and smoke test) of exec: it runs on the device and returns the cwd.
    if SCREEN_PROGRAM == "claude":
        try:
            r = await device.exec(["python3", "-c", PRETRUST_PY], timeout=10)
            if r.get("exit") == 0:
                print(f"[server] pre-trusted device cwd: {r.get('stdout', '').strip()}")
            else:
                print(f"[server] pre-trust exec failed: {r}")
        except Exception as e:
            print(f"[server] pre-trust exec error: {e}")

    sid = "s" + uuid.uuid4().hex[:8]
    screen = Screen(sid, device_id, SCREEN_COMMAND)
    device.screens[sid] = screen
    BY_SCREEN_TOKEN[screen.token] = screen
    await device.send(
        {
            "t": "session.create",
            "sid": sid,
            "command": screen.command,
            "screen": screen.token,  # becomes CHEESE_SCREEN inside the screen
            "cols": 120,
            "rows": 32,
            "source": CHEESELET,
        }
    )
    await broadcast_ui(dashboard_state())
    return JSONResponse({"sid": sid})


class SayBody(BaseModel):
    text: str


@app.post("/ui/screen/{device_id}/{sid}/say", include_in_schema=False)
async def ui_say(device_id: str, sid: str, body: SayBody) -> dict[str, Any]:
    """Chat: forward a user message to the Claude in this screen (via the
    cheeselet's say()), telling Claude how to answer back through cheese api."""
    device = DEVICES.get(device_id)
    screen = device.screens.get(sid) if device else None
    if device is None or device.ws is None or screen is None:
        raise HTTPException(status_code=404, detail="screen not connected")
    screen.chat.append({"role": "user", "text": body.text})
    device.call_seq += 1
    prompt = (
        f"{body.text}\n"
        "（这是网页聊天，用户只看得到你用 `cheese api post-note 'text: ...'` 发出的话；"
        "开始/进展/完成时都简短发一句）"
    )
    await device.send({"t": "rpc.call", "sid": sid, "id": f"srv{device.call_seq}",
                       "name": "say", "args": [prompt]})
    await broadcast_ui(dashboard_state())
    return {"ok": True}


class ChooseBody(BaseModel):
    n: int


@app.post("/ui/screen/{device_id}/{sid}/choose", include_in_schema=False)
async def ui_choose(device_id: str, sid: str, body: ChooseBody) -> dict[str, Any]:
    """The human clicked one of Claude's dialog options in the web chat; ask the
    driver to actually make that selection in Claude's terminal dialog."""
    device = DEVICES.get(device_id)
    screen = device.screens.get(sid) if device else None
    if device is None or device.ws is None or screen is None:
        raise HTTPException(status_code=404, detail="screen not connected")
    label = ""
    for c in (screen.vars.get("choices") or []):
        if c.get("n") == body.n:
            label = c.get("label", "")
    screen.chat.append({"role": "user", "text": f"[chose] {body.n}. {label}"})
    device.call_seq += 1
    await device.send({"t": "rpc.call", "sid": sid, "id": f"srv{device.call_seq}",
                       "name": "choose", "args": [body.n]})
    await broadcast_ui(dashboard_state())
    return {"ok": True}


@app.post("/ui/screen/{device_id}/{sid}/compact", include_in_schema=False)
async def ui_compact(device_id: str, sid: str) -> dict[str, Any]:
    """Ask the driver to run Claude's /compact; progress shows via the `compact`
    variable the cheeselet publishes."""
    device = DEVICES.get(device_id)
    screen = device.screens.get(sid) if device else None
    if device is None or device.ws is None or screen is None:
        raise HTTPException(status_code=404, detail="screen not connected")
    device.call_seq += 1
    await device.send({"t": "rpc.call", "sid": sid, "id": f"srv{device.call_seq}",
                       "name": "compact", "args": []})
    return {"ok": True}


@app.post("/ui/screen/{device_id}/{sid}/reload-driver", include_in_schema=False)
async def ui_reload_driver(device_id: str, sid: str) -> dict[str, Any]:
    """Hot-reload the cheeselet from disk into the running screen — no restart,
    no lost Claude context. This is the whole point of a server-delivered driver:
    fix the driver, push it, done."""
    device = DEVICES.get(device_id)
    screen = device.screens.get(sid) if device else None
    if device is None or device.ws is None or screen is None:
        raise HTTPException(status_code=404, detail="screen not connected")
    source = (ROOT / "cheeselet" / "claude.js").read_text()
    await device.send({"t": "script.load", "sid": sid, "source": source})
    return {"ok": True}


@app.websocket("/ui/screen/{device_id}/{sid}")
async def ui_screen(ws: WebSocket, device_id: str, sid: str) -> None:
    """Relay one browser terminal <-> one device screen (raw bytes both ways)."""
    await ws.accept()
    device = DEVICES.get(device_id)
    screen = device.screens.get(sid) if device else None
    if device is None or screen is None:
        await ws.close()
        return

    first_viewer = len(screen.viewers) == 0
    screen.viewers.add(ws)
    subscribed = False  # we subscribe only once we know the viewer's size

    async def ensure_subscribed(cols: int, rows: int) -> None:
        # The device attaches a real tmux client sized to the viewer, so the
        # subscribe must carry the size. The browser sends its size first thing.
        nonlocal subscribed
        if first_viewer and not subscribed:
            subscribed = True
            await device.send({"t": "screen.subscribe", "sid": sid, "cols": cols, "rows": rows})

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:  # keystrokes
                await device.send(
                    {"t": "screen.input", "sid": sid,
                     "data": base64.b64encode(data).decode()}
                )
            elif (text := msg.get("text")) is not None:  # control (resize)
                try:
                    ctrl = json.loads(text)
                except ValueError:
                    continue
                if ctrl.get("type") == "resize":
                    cols, rows = ctrl.get("cols", 120), ctrl.get("rows", 32)
                    if first_viewer and not subscribed:
                        await ensure_subscribed(cols, rows)
                    else:
                        await device.send({"t": "screen.resize", "sid": sid,
                                           "cols": cols, "rows": rows})
                elif ctrl.get("type") == "control":
                    # What the viewer is doing: 'passive' (read-only/auto),
                    # 'scroll', or 'full'. The driver uses it to decide when the
                    # screen is trustworthy to sample and when to buffer commands.
                    level = ctrl.get("level", "passive")
                    if level not in ("passive", "scroll", "full"):
                        level = "passive"
                    await device.send({"t": "var.set", "sid": sid,
                                       "name": "viewerLevel", "value": level})
    except WebSocketDisconnect:
        pass
    finally:
        screen.viewers.discard(ws)
        # A leaving viewer is passive (screen returns to the live bottom).
        await device.send({"t": "var.set", "sid": sid, "name": "viewerLevel", "value": "passive"})
        if not screen.viewers:
            await device.send({"t": "screen.unsubscribe", "sid": sid})


# --------------------------------------------------------------------------- #
# The device control channel: `cheese run` dials in here and stays connected.
# --------------------------------------------------------------------------- #


@app.websocket("/agent")
async def agent(ws: WebSocket) -> None:
    token = ws.headers.get("x-cheese-session", "")
    device = BY_TOKEN.get(token)
    if device is None:
        await ws.accept()
        await ws.close(code=4401)
        return
    await ws.accept()
    device.ws = ws
    await device.send({"t": "welcome", "v": PROTOCOL_VERSION})
    await broadcast_ui(dashboard_state())
    try:
        while True:
            m = await ws.receive_json()
            await handle_agent_message(device, m)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if device.ws is ws:
            device.ws = None
        await broadcast_ui(dashboard_state())


async def handle_agent_message(device: Device, m: dict[str, Any]) -> None:
    t = m.get("t")
    sid = m.get("sid", "")
    screen = device.screens.get(sid)

    if t == "hello":
        device.proto = m.get("v")
        return

    if t in ("heartbeat", "session.ready"):
        return

    if t == "exec.result":
        fut = device.exec_pending.get(m.get("id"))
        if fut and not fut.done():
            fut.set_result({"stdout": m.get("stdout", ""), "stderr": m.get("stderr", ""),
                            "exit": m.get("exit", 0), "truncated": m.get("truncated", False)})
        return

    if t == "session.error":
        NOTES.append({"text": f"[screen error] {m.get('error')}", "device": device.name, "screen": sid})
        await broadcast_ui(dashboard_state())
        return

    if t == "var.push" and screen is not None:  # cheeselet published a variable
        screen.vars[m["name"]] = m.get("value")
        await broadcast_ui(dashboard_state())
        return

    if t == "screen.data" and screen is not None:  # raw terminal bytes -> browsers
        try:
            raw = base64.b64decode(m.get("data", ""))
        except Exception:
            return
        dead = []
        for viewer in list(screen.viewers):
            try:
                await viewer.send_bytes(raw)
            except Exception:
                dead.append(viewer)
        for viewer in dead:
            screen.viewers.discard(viewer)
        return

    if t == "rpc.call":  # the cheeselet called the server (JS -> server)
        result, error = await handle_screen_call(device, screen, m.get("name", ""), m.get("args", []))
        await device.send({"t": "rpc.result", "sid": sid, "id": m.get("id"),
                           "value": result, "error": error})
        return


async def handle_screen_call(
    device: Device, screen: Screen | None, name: str, args: list[Any]
) -> tuple[Any, str]:
    if name == "screenReady":
        return {"ok": True}, ""
    return None, f"unknown function {name!r}"


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "9100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
