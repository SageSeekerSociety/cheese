"""Send a real chat message the way the frontend does, and report what came back.

Two modes:

  inspect  — the state a turn depends on: machines and their enrollment, recent
             topics, and the last blocks of one topic.
  send     — mint a session token for a real handle, open the topic's chat
             WebSocket, post one message, and print every frame with a timestamp
             until `done` (or the deadline).

The point of driving the WebSocket rather than calling the service directly is
that "can't send a message" is a claim about the path the browser takes; a
service-level call would skip auth, authorization, and the broker relay — the
three places that path can fail without leaving an exception in the log.

Run inside the backend container, which already has the app, its deps and DB
reachability:

  docker exec -w /app -e PYTHONPATH=/app cheese-backend-1 \
      python scripts/chat_send_probe.py inspect
  docker exec -w /app -e PYTHONPATH=/app -e HANDLE=... -e TOPIC_ID=... \
      cheese-backend-1 python scripts/chat_send_probe.py send

Env:
  HANDLE       handle to act as (send mode; must exist)
  TOPIC_ID     topic to post into (send mode; defaults to the newest topic)
  CONTENT      message body (default: a self-identifying probe line)
  SUMMON       "1" (default) to @芝士 so a turn actually runs
  DEADLINE_S   how long to keep reading frames (default 300)
  WS_BASE      default ws://127.0.0.1:8000
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime

import websockets
from sqlalchemy import text

from app.core.db import async_session_factory
from app.core.tokens import mint_session_token


def _stamp() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S")


def _say(*parts: object) -> None:
    print(f"{_stamp()}", *parts, flush=True)


async def _rows(sql: str, **params: object) -> list[dict]:
    async with async_session_factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row) for row in result.mappings()]


async def inspect() -> int:
    _say("== project_machines ==")
    machines = await _rows(
        """
        select m.id, m.project_id, m.machine_id, m.hostname, m.status, m.ai_mode,
               m.ai_status, m.device_id, m.enrolled_at, m.enroll_error,
               m.enroll_attempts, m.created_at, p.name as project_name
          from project_machines m left join projects p on p.id = m.project_id
         order by m.created_at desc
         limit 20
        """
    )
    if not machines:
        _say("  (none — no project has a machine)")
    for m in machines:
        _say(
            f"  {m['hostname']} [{m['project_name']}] project={m['project_id']}"
            f" machine={m['machine_id']} status={m['status']}"
            f" ai={m['ai_mode']}/{m['ai_status']} device={m['device_id']}"
            f" enrolled={m['enrolled_at']} attempts={m['enroll_attempts']}"
            + (f" err={m['enroll_error']!r}" if m["enroll_error"] else "")
        )

    # Which device is actually connected lives in the SERVING process's hub, not
    # in the DB — a separate `docker exec` sees an empty hub, so asking here
    # would only produce a confident wrong answer. The send probe below settles
    # it instead: an offline device makes the turn say so.

    _say("== topics (newest) ==")
    topics = await _rows(
        """
        select t.id, t.title, t.project_id, t.session_id, t.compute_profile,
               t.created_at, p.name as project_name
          from topics t join projects p on p.id = t.project_id
         order by t.created_at desc
         limit 10
        """
    )
    for t in topics:
        has_session = "yes" if t["session_id"] else "no"
        _say(
            f"  {t['id']} [{t['project_name']}] {t['title']!r}"
            f" profile={t['compute_profile']} session={has_session}"
            f" {t['created_at']}"
        )

    _say("== last blocks overall ==")
    blocks = await _rows(
        """
        select id, topic_id, author, author_type, kind, created_at, content
          from blocks
         order by created_at desc
         limit 15
        """
    )
    for b in blocks:
        head = str(b["content"] or "")[:160].replace("\n", " ⏎ ")
        _say(f"  {b['created_at']} [{b['kind']}] {b['author']}: {head}")
    return 0


async def send() -> int:
    handle = os.environ.get("HANDLE", "")
    if not handle:
        _say("HANDLE is required")
        return 2

    async with async_session_factory() as session:
        row = (
            (
                await session.execute(
                    text('select id, username from "user" where username = :h'),
                    {"h": handle},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        _say(f"no user with handle {handle!r}")
        return 2
    user_id = int(row["id"])

    topic_id = os.environ.get("TOPIC_ID", "")
    if not topic_id:
        newest = await _rows("select id from topics order by created_at desc limit 1")
        if not newest:
            _say("no topics exist")
            return 2
        topic_id = str(newest[0]["id"])
    uuid.UUID(topic_id)

    token = mint_session_token(handle=handle, user_id=user_id)
    base = os.environ.get("WS_BASE", "ws://127.0.0.1:8000")
    url = f"{base}/topics/{topic_id}/chat?token={token}"
    content = os.environ.get("CONTENT", "probe: 请回复 pong 并说明你运行在哪台机器上")
    summon = os.environ.get("SUMMON", "1") == "1"
    deadline = time.time() + float(os.environ.get("DEADLINE_S", "300"))

    _say(f"connecting as {handle} (uid={user_id}) to topic {topic_id}")
    async with websockets.connect(url, max_size=None) as ws:
        _say("connected")
        await ws.send(
            json.dumps(
                {
                    "type": "message",
                    "content": content,
                    "author": handle,
                    "summon": summon,
                }
            )
        )
        _say(f"sent (summon={summon}): {content!r}")
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except TimeoutError:
                _say("DEADLINE reached with no `done` frame")
                return 1
            except websockets.ConnectionClosed as exc:
                _say(f"SOCKET CLOSED code={exc.code} reason={exc.reason!r}")
                return 1
            frame = json.loads(raw)
            kind = frame.get("type")
            if kind in {"assistant_block", "user_block", "event_block"}:
                block = frame.get("block") or {}
                body = str(block.get("content") or "")[:400].replace("\n", " ⏎ ")
                _say(f"< {kind} [{block.get('author')}] {body}")
            elif kind == "error":
                _say(f"< ERROR {frame.get('message')!r}")
            elif kind == "done":
                _say("< done")
                return 0
            else:
                _say(f"< {kind} {json.dumps(frame, ensure_ascii=False)[:300]}")
    _say("DEADLINE reached")
    return 1


async def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    if mode == "inspect":
        return await inspect()
    if mode == "send":
        return await send()
    _say(f"unknown mode {mode!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
