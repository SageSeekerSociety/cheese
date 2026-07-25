"""End-to-end smoke test for SELF-HOSTED (device) turns — runs INSIDE the backend
container on the box that also hosts the enrolled device.

It drives one real summoned turn on a device-backed project and asserts the three
properties the first dev self-hosting run got wrong:

  #94  the screen edits the topic's REAL worktree (co-located devices), and the
       backend snapshots it — so 采纳/diff see the work;
  #95  the agent can file its own 验收卡 with the `cheese` CLI shipped to the device;
  #96  the chat WebSocket relays frames to a client while the turn runs.

Usage (from the box, via the dev runner or by hand):

    docker cp backend/scripts/device_selfhost_smoke.py cheese-backend-1:/tmp/
    docker exec cheese-backend-1 python /tmp/device_selfhost_smoke.py

Env: PROJECT_ID (explicit, wins), PROJECT_NAME (default "cheese 自建"),
USER_HANDLE (default "andy"),
BASE (default http://localhost:8081), TURN_TIMEOUT_S (default 600).
Exit code 0 only when every check passes; the summary lists PASS/FAIL per check.
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("BASE", "http://localhost:8081").rstrip("/")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "cheese 自建")
USER_HANDLE = os.environ.get("USER_HANDLE", "andy")
# Explicit id wins over the name lookup (unambiguous when a box has retry dupes).
PROJECT_ID = os.environ.get("PROJECT_ID", "").strip()
TURN_TIMEOUT_S = float(os.environ.get("TURN_TIMEOUT_S", "600"))
MARKER = f"device-smoke {uuid.uuid4().hex[:8]}"

_CARD_CMD = f'cheese accept-request {USER_HANDLE} "设备自托管冒烟 {MARKER}"'
PROMPT = (
    "请做两件小事,不要做别的、不要跑测试:\n"
    f"1. 在 README.md 末尾追加一行:`<!-- {MARKER} -->`\n"
    f"2. 然后执行 `{_CARD_CMD}` 递验收卡。\n"
    "做完直接结束。"
)


def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


async def main() -> int:
    from sqlalchemy import select

    from app.core.db import async_session_factory
    from app.core.tokens import mint_session_token
    from app.domain.device.models import DeviceProjectRow
    from app.domain.project.models import Project
    from app.domain.user.models import User
    from app.domain.workspace import service as ws

    # 1. Resolve the actor + the device-backed project straight from the DB, so the
    #    smoke test needs no password and no hand-recorded ids.
    async with async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.username == USER_HANDLE))
        ).scalar_one_or_none()
        if user is None:
            print(f"FAIL setup: no user {USER_HANDLE!r} on this deployment")
            return 2
        if PROJECT_ID:
            project_id = uuid.UUID(PROJECT_ID)
        else:
            # A name can match more than one project (dev boxes accumulate retries),
            # so prefer one with a device actually bound to it — that is the project
            # this test is about — and fall back to the newest match.
            candidates = list(
                (
                    await session.execute(
                        select(Project)
                        .where(Project.name == PROJECT_NAME)
                        .order_by(Project.created_at.desc())
                    )
                ).scalars()
            )
            if not candidates:
                print(f"FAIL setup: no project {PROJECT_NAME!r} on this deployment")
                return 2
            bound = set(
                (
                    await session.execute(
                        select(DeviceProjectRow.project_id).where(
                            DeviceProjectRow.project_id.in_([p.id for p in candidates])
                        )
                    )
                ).scalars()
            )
            chosen = next((p for p in candidates if p.id in bound), candidates[0])
            if len(candidates) > 1:
                why = "device-bound" if chosen.id in bound else "newest"
                print(
                    f"note: {len(candidates)} projects named {PROJECT_NAME!r}; "
                    f"picked {chosen.id} ({why})",
                    flush=True,
                )
            project_id = chosen.id
        token = mint_session_token(handle=user.username, user_id=user.id)

    print(f"project={project_id} actor={USER_HANDLE} marker={MARKER}", flush=True)

    topic_id = uuid.UUID(
        api(
            "POST",
            "/api/topics",
            token,
            {"project_id": str(project_id), "title": f"设备自托管冒烟 {MARKER}"},
        )["data"]["id"]
    )
    print(f"topic={topic_id}", flush=True)

    # 2. Drive one summoned turn over the chat WS, recording every relayed frame
    #    (that recording IS the #96 check).
    import websockets

    frames: list[dict] = []
    url = f"{BASE.replace('http://', 'ws://').replace('https://', 'wss://')}"
    url += f"/api/topics/{topic_id}/chat?token={token}"
    async with websockets.connect(url, open_timeout=20) as sock:
        await sock.send(
            json.dumps({"type": "message", "content": PROMPT, "summon": True})
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + TURN_TIMEOUT_S
        # Only AGENT output starts the idle clock: a device turn echoes
        # user_block/turn_active instantly and then thinks silently for a minute
        # or two, so counting those would cut the turn off before it works.
        idle_after_output = 90.0
        agent_frames = 0
        last = loop.time()
        while loop.time() < deadline:
            try:
                frame = json.loads(
                    await asyncio.wait_for(
                        sock.recv(), timeout=min(5.0, max(1.0, deadline - loop.time()))
                    )
                )
            except TimeoutError:
                if agent_frames and (loop.time() - last) > idle_after_output:
                    break
                continue
            frames.append(frame)
            kind_now = frame.get("type")
            if kind_now in ("assistant_block", "event_block", "block_updated"):
                agent_frames += 1
                last = loop.time()
            kind = frame.get("type")
            block = frame.get("block") or {}
            text = (block.get("content") or "")[:90].replace("\n", " ")
            print(f"  [{kind}] {text}", flush=True)

    # 3. Assert the three properties.
    results: list[tuple[str, bool, str]] = []

    results.append(
        (
            "#96 chat WS relays frames",
            bool(frames),
            f"{len(frames)} frames ({agent_frames} from the agent)",
        )
    )

    # The worktree snapshot and the card land as the turn finishes; give them a
    # short grace window rather than reading the instant the stream goes quiet.
    for _ in range(20):
        cards_now = api("GET", f"/api/topics/{topic_id}/accept-card", token)["data"]
        wt_now = ws.topic_worktree(project_id, topic_id) / "README.md"
        seen = wt_now.exists() and MARKER in wt_now.read_text(
            encoding="utf-8", errors="ignore"
        )
        if seen and int(cards_now.get("total") or 0) > 0:
            break
        await asyncio.sleep(6)

    worktree = ws.topic_worktree(project_id, topic_id)
    readme = worktree / "README.md"
    edited = readme.exists() and MARKER in readme.read_text(
        encoding="utf-8", errors="ignore"
    )
    results.append(
        (
            "#94 device edited the topic's real worktree",
            edited,
            f"{readme} {'contains' if edited else 'MISSING'} the marker",
        )
    )

    cards = api("GET", f"/api/topics/{topic_id}/accept-card", token)["data"]
    filed = int(cards.get("total") or 0) > 0
    results.append(
        (
            "#95 agent filed its own accept card (cheese CLI on device)",
            filed,
            f"{cards.get('total')} card(s)",
        )
    )

    print("\n=== device self-hosting smoke ===", flush=True)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name} — {detail}", flush=True)
    return 0 if all(ok for _, ok, _ in results) else 1


try:
    sys.exit(asyncio.run(main()))
except urllib.error.HTTPError as exc:  # noqa: BLE001 — report, don't traceback
    print(f"FAIL http {exc.code}: {exc.read()[:300]!r}")
    sys.exit(2)
