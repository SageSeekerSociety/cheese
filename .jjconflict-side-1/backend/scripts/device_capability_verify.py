"""Verify what the DEVICE just did, without any model spend.

Given a topic id, reports (as `KEY=value` lines) the two facts the device
capability checks care about:

    CARDS=<accept cards filed on the topic>      # the shipped `cheese` CLI works
    MARKER=<1|0>                                 # the device wrote into the topic's
                                                 # REAL worktree (co-located #94)

Usage: python device_capability_verify.py <topic_id> [marker]
"""

import asyncio
import json
import os
import sys
import urllib.request
import uuid

BASE = os.environ.get("BASE", "http://localhost:8081").rstrip("/")
USER_HANDLE = os.environ.get("USER_HANDLE", "andy")
TOPIC_ID = uuid.UUID(sys.argv[1])
MARKER = sys.argv[2] if len(sys.argv) > 2 else ""


async def main() -> int:
    from sqlalchemy import select

    from app.common.auth import create_access_token
    from app.core.db import async_session_factory
    from app.domain.topic.models import Topic
    from app.domain.user.models import User
    from app.domain.workspace import service as ws

    async with async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.username == USER_HANDLE))
        ).scalar_one_or_none()
        if user is None:
            print("CARDS=0")
            print("MARKER=0")
            return 2
        token = create_access_token(user.id, handle=user.username)
        topic = (
            await session.execute(select(Topic).where(Topic.id == TOPIC_ID))
        ).scalar_one_or_none()
        project_id = topic.project_id if topic is not None else None

    req = urllib.request.Request(
        f"{BASE}/api/topics/{TOPIC_ID}/accept-card",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        cards = json.loads(resp.read())["data"]
    print(f"CARDS={int(cards.get('total') or 0)}")

    seen = 0
    if MARKER and project_id is not None:
        probe = ws.topic_worktree(project_id, TOPIC_ID) / "DEVICE_PROBE.txt"
        if probe.exists() and MARKER in probe.read_text(
            encoding="utf-8", errors="ignore"
        ):
            seen = 1
    print(f"MARKER={seen}")
    return 0


sys.exit(asyncio.run(main()))
