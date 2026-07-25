"""Print how many enrolled devices the RUNNING backend currently sees as online.

Run inside the backend container. It asks the live server over its own API
(/connector/my/devices) rather than importing `device_hub`: that registry lives
in the server process, so importing it here would always show an empty hub.

Prints a single integer (0 on any failure) so a shell probe can branch on it:

    docker exec -w /app -e PYTHONPATH=/app cheese-backend-1 \\
      python /tmp/device_online_probe.py
"""

import asyncio
import json
import os
import sys
import urllib.request

BASE = os.environ.get("BASE", "http://localhost:8081").rstrip("/")
USER_HANDLE = os.environ.get("USER_HANDLE", "andy")


async def count_online() -> int:
    from sqlalchemy import select

    from app.core.db import async_session_factory
    from app.core.tokens import mint_session_token
    from app.domain.user.models import User

    async with async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.username == USER_HANDLE))
        ).scalar_one_or_none()
        if user is None:
            print(f"no user {USER_HANDLE!r}", file=sys.stderr)
            return 0
        token = mint_session_token(handle=user.username, user_id=user.id)

    req = urllib.request.Request(
        f"{BASE}/connector/my/devices", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        devices = json.loads(resp.read()).get("devices", [])
    for device in devices:
        print(
            f"device {device.get('device_id')} online={device.get('online')} "
            f"projects={len(device.get('project_ids') or [])}",
            file=sys.stderr,
        )
    return sum(1 for d in devices if d.get("online"))


try:
    print(asyncio.run(count_online()))
except Exception as exc:  # noqa: BLE001 — a probe must always print a number
    print(f"probe failed: {exc}", file=sys.stderr)
    print(0)
