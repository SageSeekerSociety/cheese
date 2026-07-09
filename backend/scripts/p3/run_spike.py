"""P3 connector spike — Phase A end-to-end proof (reproducible).

Proves the self-hosted slice with the REAL frozen Go cli acting as the "user machine"
and our backend modules:

  1. start our standalone connector backend (spike_app) on 127.0.0.1:PORT;
  2. device flow: HTTP ``/connector/auth/device/start`` → code; approve in-process
     (simulating the human ``/connect``); HTTP ``/connector/auth/device/poll`` → token;
  3. write the cli config and run the FROZEN cli (`cheese run`) — it DIALS OUT to
     ``WS /connector/agent`` and authenticates with the device token (NAT-friendly);
  4. open a screen on the device running interactive ``claude`` with OUR hooks;
  5. attach a viewer (proves the raw screen relay — 现场) and send one prompt via the
     minimal cheeselet;
  6. collect the structured hook events flowing back (SessionStart → … → Stop) and the
     relayed screen bytes; write evidence to ``scripts/p3/logs/``.

Everything runs in ONE Python process (server + hub + orchestrator); the cli is a
separate process — the real device. Nothing here touches the running :8099 stack.

Usage (from backend/):  uv run python scripts/p3/run_spike.py
"""

import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import uvicorn

# Make ``app`` + ``scripts.p3`` importable when run as a script from backend/.
_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))

from app.core.config import settings  # noqa: E402
from app.core.sandbox_auth import mint_scoped_token  # noqa: E402
from app.domain.agent.device_hub import device_hub  # noqa: E402
from app.domain.agent.device_launch import build_screen_launch  # noqa: E402
from app.domain.agent.hook_events import hook_router, translate_hook  # noqa: E402
from app.domain.agent.service import (  # noqa: E402
    AgentMessage,
    AgentResult,
    AgentToolUse,
)
from scripts.p3.spike_app import build_spike_app  # noqa: E402

PORT = 8770
BASE = f"http://127.0.0.1:{PORT}"
CLI_BIN = "/tmp/cheese-agent-layer-analysis/cheese-cli"
LOG_DIR = Path(__file__).resolve().parent / "logs"
# device-side home/work (this Mac IS the device)
RUN_DIR = Path("/tmp/cheesex-p3-spike")


def log(msg: str) -> None:
    line = f"[{datetime.now(UTC).strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(line, flush=True)
    with (LOG_DIR / "spike.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


class CollectingViewer:
    """A fake browser viewer: the raw screen bytes the hub relays land here, proving
    the 现场 channel works end-to-end (we do NOT parse them)."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.chunks.append(data)

    @property
    def total(self) -> int:
        return sum(len(c) for c in self.chunks)


async def _wait(cond, timeout: float, poll: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(poll)
    return cond()


async def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "spike.log").write_text("", encoding="utf-8")
    if not Path(CLI_BIN).is_file():
        log(f"FAIL: cli binary not found at {CLI_BIN} (build it first)")
        return 2

    project_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    owner_user_id = uuid.uuid4()
    agent_user_id = uuid.uuid4()
    evidence: dict[str, object] = {
        "project_id": str(project_id),
        "topic_id": str(topic_id),
    }

    # 1. Start the standalone connector backend in-process.
    app, device_service = build_spike_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    if not await _wait(lambda: server.started, timeout=15):
        log("FAIL: spike server did not start")
        return 2
    log(f"connector backend up at {BASE} (in-memory device repo; :8099 untouched)")

    async with httpx.AsyncClient(base_url=BASE, timeout=10) as client:
        # 2. Device flow: real HTTP start → approve (simulate human) → real HTTP poll.
        r = await client.post(
            "/connector/auth/device/start", json={"device_name": "andyl-macbook"}
        )
        r.raise_for_status()
        code = r.json()["device_code"]
        log(f"POST /connector/auth/device/start → code={code[:8]}… approve_url ok")

        device = await device_service.approve(
            code, owner_user_id=owner_user_id, agent_user_id=agent_user_id
        )
        log(f"approved (human /connect simulated) → device_id={device.device_id}")

        r = await client.post(
            "/connector/auth/device/poll", json={"device_code": code}
        )
        r.raise_for_status()
        poll = r.json()
        assert poll["status"] == "approved" and poll["token"], poll
        token = poll["token"]
        log("POST /connector/auth/device/poll → approved + durable token")
        evidence["device_id"] = device.device_id

    # 3. Write cli config + run the FROZEN cli (real dial-out).
    cfg_dir = RUN_DIR / "cli-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text(
        json.dumps(
            {"base": f"{BASE}/connector", "token": token, "device_id": device.device_id}
        ),
        encoding="utf-8",
    )
    cli_env = dict(os.environ)
    # Ensure the cli (and the tmux it spawns) can find bash/claude/node/tmux/curl.
    extra_path = (
        "/Users/andyl/.local/bin:/etc/profiles/per-user/andyl/bin:"
        "/run/current-system/sw/bin:/usr/bin:/bin"
    )
    cli_env["PATH"] = extra_path + ":" + cli_env.get("PATH", "")
    cli_log = (LOG_DIR / "cli.log").open("w", encoding="utf-8")
    cli_proc = await asyncio.create_subprocess_exec(
        CLI_BIN, "run", "--config", str(cfg_path),
        stdout=cli_log, stderr=asyncio.subprocess.STDOUT, env=cli_env,
    )
    log(f"launched frozen cli: {CLI_BIN} run (pid={cli_proc.pid}) — dialing out…")

    try:
        if not await _wait(lambda: device_hub.is_online(device.device_id), timeout=20):
            log("FAIL: cli did not connect to WS /connector/agent (device offline)")
            return 2
        log("✅ cli DIALED OUT and connected: WS /connector/agent, device ONLINE")
        evidence["cli_connected"] = True

        # 4. Open a screen: interactive `claude` with OUR hooks (device-side launcher).
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        home_dir = str(RUN_DIR / "home" / str(project_id))
        work_dir = str(RUN_DIR / "work" / str(project_id) / str(topic_id))
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        hook_token = mint_scoped_token(
            project_id=str(project_id), topic_id=str(topic_id), ttl_s=3600
        )
        hook_url = f"{BASE}/sandbox/hooks/{topic_id}"
        command, screen_env, cheeselet = build_screen_launch(
            hook_url=hook_url,
            hook_token=hook_token,
            home_dir=home_dir,
            work_dir=work_dir,
            model=settings.agent_model,
            extra_env=settings.agent_env(),
        )
        # Register the topic's hook queue BEFORE the screen so no hook is missed.
        queue = hook_router.register(str(topic_id))
        screen = await device_hub.open_screen(
            device.device_id,
            command,
            cheeselet,
            agent_user_id=agent_user_id,
            agent_handle="agent-spike",
            project_id=project_id,
            topic_id=topic_id,
            hook_key=str(topic_id),
            env=screen_env,
        )
        log(f"opened screen sid={screen.sid} (claude + hooks) on the device")

        # 5. Attach a viewer (proves the raw-screen relay) + send one prompt.
        viewer = CollectingViewer()
        await device_hub.attach_viewer(device.device_id, screen.sid, viewer)
        log("attached viewer → screen.subscribe sent (现场 relay armed)")

        prompt = "只回答一个数字，别的都不要说：1+1 等于几？"
        call_id = await device_hub.call_screen(
            device.device_id, screen.sid, "prompt", [prompt]
        )
        log(f"sent prompt via cheeselet.prompt() (call {call_id}); await ack…")
        try:
            await device_hub.await_call(device.device_id, call_id, timeout=90)
            log("cheeselet acked prompt (typed into claude's input box)")
        except Exception as exc:  # noqa: BLE001 — log + keep draining hooks
            log(f"WARN: prompt ack failed/timed out: {exc}")

        # 6. Drain structured hook events (perception via OUR hooks).
        events: list[str] = []
        got_session = False
        got_result = False
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                hook = await asyncio.wait_for(
                    queue.get(), timeout=deadline - time.monotonic()
                )
            except TimeoutError:
                break
            name = hook.get("hook_event_name") or hook.get("hookEventName") or "?"
            event = translate_hook(hook)
            kind = type(event).__name__ if event is not None else "—"
            summary = ""
            if isinstance(event, AgentToolUse):
                summary = f" tool={event.name}"
            elif isinstance(event, AgentMessage | AgentResult):
                summary = f" text={event.text[:60].replace(chr(10), ' ')!r}"
            log(f"  hook {name:14} -> {kind}{summary}")
            events.append(f"{name}->{kind}")
            if name == "SessionStart":
                got_session = True
            if isinstance(event, AgentResult):
                got_result = True
                evidence["final_text"] = str(getattr(event, "text", ""))[:200]
                break

        hook_router.unregister(str(topic_id), queue)

        # Give the relay a moment to flush the final frames, then snapshot evidence.
        await asyncio.sleep(1.0)
        evidence["hook_events"] = events
        evidence["got_session_start"] = got_session
        evidence["got_result_stop"] = got_result
        evidence["screen_relay_bytes"] = viewer.total
        (LOG_DIR / "screen_relay.bin").write_bytes(b"".join(viewer.chunks))
        (LOG_DIR / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        log("── EVIDENCE ─────────────────────────────────────────────")
        log(f"  cli dialed out + connected : {evidence.get('cli_connected')}")
        log(f"  hooks: SessionStart        : {got_session}")
        log(f"  hooks: Stop/AgentResult    : {got_result}")
        log(f"  hook event sequence        : {' '.join(events) or '(none)'}")
        log(f"  screen relay bytes (现场)  : {viewer.total}")
        if got_result:
            log(f"  final answer (last msg)    : {evidence.get('final_text')!r}")
        ok = bool(evidence.get("cli_connected")) and got_session and viewer.total > 0
        log(f"  PHASE A RESULT             : {'PASS' if ok else 'PARTIAL/FAIL'}")
        log("─────────────────────────────────────────────────────────")

        try:
            await device_hub.close_screen(device.device_id, screen.sid)
        except Exception:  # noqa: BLE001
            pass
        return 0 if ok else 1
    finally:
        cli_proc.terminate()
        try:
            await asyncio.wait_for(cli_proc.wait(), timeout=8)
        except TimeoutError:
            cli_proc.kill()
        cli_log.close()
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=8)
        except TimeoutError:
            pass
        # Best-effort: leave logs, clean the transient run dir.
        shutil.rmtree(RUN_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
