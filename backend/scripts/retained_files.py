"""List and fetch the files the platform keeps for a while before deleting them.

    python -m scripts.retained_files rooms list
    python -m scripts.retained_files rooms download <key> [--out PATH]

For an operator on a shell of the box, when someone asks for files back: the
working files of rooms from before rooms had an executor, which their cleanup
sent to the private bucket and keeps until the date it told the room
(``agent/device_storage.py``); that is the ``rooms`` group.

``rooms list`` prints one line per copy still kept: its key, size, the date it is
deleted, and the room and task it belonged to. ``rooms download`` writes one
of those copies, a gzipped tar of the room's directory, to ``--out`` (default: the
key's file name in the current directory). The bytes come straight from the
bucket by a short-lived URL; only keys ``rooms list`` shows can be fetched. Exits 1
for a key that is not kept.
"""

import argparse
import asyncio
import shutil
import sys
import urllib.request
from pathlib import Path

from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.storage import private_storage
from app.domain.topic.models import KeptRoomFiles

URL_TTL_S = 3600


async def kept() -> list[KeptRoomFiles]:
    async with async_session_factory() as session:
        return list(
            await session.scalars(
                select(KeptRoomFiles)
                .where(
                    KeptRoomFiles.key.is_not(None), KeptRoomFiles.deleted_at.is_(None)
                )
                .order_by(KeptRoomFiles.expires_at, KeptRoomFiles.key)
            )
        )


async def list_kept() -> int:
    for row in await kept():
        assert row.expires_at is not None
        print(
            row.key,
            row.size,
            row.expires_at.date().isoformat(),
            f"room={row.room_id or '-'}",
            f"task={row.task_id or '-'}",
            sep="\t",
        )
    return 0


async def download(key: str, out: Path | None) -> int:
    if key not in {row.key for row in await kept()}:
        print(f"{key} not_kept", file=sys.stderr)
        return 1
    url = await private_storage().presign(key, "get_object", URL_TTL_S)
    target = out or Path(key.rsplit("/", 1)[-1])
    with urllib.request.urlopen(url, timeout=600) as response, target.open("wb") as f:
        shutil.copyfileobj(response, f, 1 << 20)
    print(f"{key} -> {target} ({target.stat().st_size} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="The files kept before deletion.")
    groups = parser.add_subparsers(dest="group", required=True)
    rooms = groups.add_parser("rooms", help="old rooms' working files")
    commands = rooms.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    fetch = commands.add_parser("download")
    fetch.add_argument("key")
    fetch.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.command == "list":
        return asyncio.run(list_kept())
    return asyncio.run(download(args.key, args.out))


if __name__ == "__main__":
    sys.exit(main())
