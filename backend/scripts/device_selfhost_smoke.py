"""End-to-end smoke test for SELF-HOSTED (device) turns — runs INSIDE the backend
container on the box that also hosts the enrolled device.

It drives one real summoned turn on a device-backed project and asserts the three
properties the first dev self-hosting run got wrong:

  #94  the screen edits its device-owned checkout and pushes the task branch,
       so 采纳/diff see the work;
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
import subprocess
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

_CARD_CMD = (
    f'cheese accept-request {USER_HANDLE} "设备自托管冒烟 {MARKER}" '
    '--subject "test: verify device task delivery"'
)
# An override lets CI drive a REAL dogfood round (any task), not just the marker
# check; the built-in prompt stays the default so the assertions still apply.
PROMPT = os.environ.get("PROMPT", "").strip() or (
    "请做两件小事,不要做别的、不要跑测试:\n"
    f"1. 在 README.md 末尾追加一行:`<!-- {MARKER} -->`\n"
    f"2. 提交并用 cheese sync 同步成功后，执行 `{_CARD_CMD}` 递验收卡。\n"
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

    from app.common.auth import create_access_token
    from app.core.db import async_session_factory
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
        # Mint what the product mints at login, so every endpoint the browser
        # can reach (including /connector/*) accepts this token too.
        token = create_access_token(user.id, handle=user.username)

    print(f"project={project_id} actor={USER_HANDLE} marker={MARKER}", flush=True)

    topic_id = uuid.UUID(
        api(
            "POST",
            "/topics",
            token,
            {"project_id": str(project_id), "title": f"设备自托管冒烟 {MARKER}"},
        )["data"]["id"]
    )
    repo = ws.ensure_repo(project_id)
    task = api(
        "POST",
        f"/topics/{topic_id}/split",
        token,
        {"title": f"Device smoke {MARKER}", "reviewer_handle": USER_HANDLE},
    )["data"]
    branch = task["branch_name"]

    def branch_head() -> str:
        return subprocess.run(
            ["git", "rev-parse", branch],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    def branch_readme() -> str:
        result = subprocess.run(
            ["git", "show", f"{branch}:README.md"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""

    before_head = branch_head()
    print(f"topic={topic_id} branch={branch} base={before_head}", flush=True)

    # 2. Drive one summoned turn over the chat WS, recording every relayed frame
    #    (that recording IS the #96 check).
    import websockets

    frames: list[dict] = []
    url = f"{BASE.replace('http://', 'ws://').replace('https://', 'wss://')}"
    url += f"/topics/{topic_id}/chat?token={token}"
    async with websockets.connect(url, open_timeout=20) as sock:
        await sock.send(
            json.dumps(
                {
                    "type": "message",
                    "content": f'先执行 cd "$(cheese worktree {task["id"]})"，'
                    "在这条已有任务中完成：\n" + PROMPT,
                    "summon": True,
                }
            )
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + TURN_TIMEOUT_S
        # The idle clock starts on AGENT ACTIVITY — assistant text OR tool use.
        # A device turn echoes user_block/turn_active instantly and then thinks
        # silently, so those must not start it; but counting only assistant text
        # cut off a turn that was busily running tools (it got killed mid-task,
        # before it could file its card).
        idle_after_output = 90.0
        agent_frames = 0
        activity = 0
        last = loop.time()
        while loop.time() < deadline:
            try:
                frame = json.loads(
                    await asyncio.wait_for(
                        sock.recv(), timeout=min(5.0, max(1.0, deadline - loop.time()))
                    )
                )
            except TimeoutError:
                if activity and (loop.time() - last) > idle_after_output:
                    break
                continue
            frames.append(frame)
            kind_now = frame.get("type")
            if kind_now in ("assistant_block", "event_block", "block_updated"):
                agent_frames += 1
                activity += 1
                last = loop.time()
            elif kind_now == "tool":
                activity += 1
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

    # The device push and the card land as the request finishes; give them a short
    # grace window rather than reading the instant the stream goes quiet.
    for _ in range(20):
        cards_now = api("GET", f"/topics/{topic_id}/accept-card", token)["data"]
        seen = MARKER in branch_readme()
        if seen and int(cards_now.get("total") or 0) > 0:
            break
        await asyncio.sleep(6)

    after_head = branch_head()
    pushed = bool(after_head) and after_head != before_head
    custom = bool(os.environ.get("PROMPT", "").strip())
    edited = MARKER in branch_readme()
    if custom:
        # A caller-supplied task may write anything; a changed branch head proves
        # that the device committed and pushed its work back.
        results.append(
            (
                "#94 device pushed the task branch",
                pushed,
                f"{before_head[:12]} -> {after_head[:12]}",
            )
        )
    else:
        results.append(
            (
                "#94 device pushed its edit into the task branch",
                pushed and edited,
                f"{branch}:README.md {'contains' if edited else 'MISSING'} the marker",
            )
        )

    cards = api("GET", f"/topics/{topic_id}/accept-card", token)["data"]
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
