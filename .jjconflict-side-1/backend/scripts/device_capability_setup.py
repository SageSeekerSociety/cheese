"""Prepare a scratch topic for the zero-spend device capability checks.

Creates a topic in the device-bound project and prints the ids plus the scoped
token a device screen would receive, as shell-friendly `KEY=value` lines:

    PROJECT=<uuid>
    TOPIC=<uuid>
    TOKEN=<scoped token>
    WORKTREE=<container path of the topic worktree>
    HOSTTREE=<same worktree as the co-located DEVICE sees it>

No agent turn is summoned, so this costs nothing at the model provider.
"""

import asyncio
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("BASE", "http://localhost:8081").rstrip("/")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "cheese 自建")
USER_HANDLE = os.environ.get("USER_HANDLE", "andy")


async def main() -> int:
    from sqlalchemy import select

    from app.common.auth import create_access_token
    from app.core.config import settings
    from app.core.db import async_session_factory
    from app.core.sandbox_auth import mint_scoped_token
    from app.domain.device.models import DeviceProjectRow
    from app.domain.project.models import Project
    from app.domain.user.models import User
    from app.domain.workspace import service as ws

    async with async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.username == USER_HANDLE))
        ).scalar_one_or_none()
        if user is None:
            print(f"no user {USER_HANDLE!r}", file=sys.stderr)
            return 2
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
            print(f"no project {PROJECT_NAME!r}", file=sys.stderr)
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
        project = next((p for p in candidates if p.id in bound), candidates[0])
        token = create_access_token(user.id, handle=user.username)

    req = urllib.request.Request(
        f"{BASE}/api/topics",
        data=json.dumps(
            {"project_id": str(project.id), "title": "设备能力检查(零消耗)"}
        ).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        topic_id = uuid.UUID(json.loads(resp.read())["data"]["id"])

    worktree = ws.topic_worktree(project.id, topic_id).resolve()
    host_root = settings.device_shared_workspace_host_root.strip()
    hosttree = ""
    if host_root:
        try:
            rel = worktree.relative_to(Path(settings.workspace_root).resolve())
            hosttree = str(Path(host_root) / rel)
        except ValueError:
            hosttree = ""

    scoped = mint_scoped_token(project_id=str(project.id), topic_id=str(topic_id))
    print(f"PROJECT={project.id}")
    print(f"TOPIC={topic_id}")
    print(f"TOKEN={scoped}")
    print(f"WORKTREE={worktree}")
    print(f"HOSTTREE={hosttree}")
    return 0


sys.exit(asyncio.run(main()))
