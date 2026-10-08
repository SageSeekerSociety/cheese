"""List and fetch the files the platform keeps for a while before deleting them.

    python -m scripts.retained_files rooms list
    python -m scripts.retained_files rooms download <key> [--out PATH]
    python -m scripts.retained_files homes list [--conversation ID] [--session ID]
    python -m scripts.retained_files homes download <key> [--out PATH]

For an operator on a shell of the box, when someone asks for files back. Two
kinds are kept, each until the date its room was told:

- ``rooms``: the working files of rooms from before rooms had an executor,
  which their cleanup sent to the private bucket (``agent/device_storage.py``).
- ``homes``: archives of cloud sandbox homes, written while idle sandboxes
  were archived instead of destroyed (``machine/retained_archives.py``, which
  deletes them after their date). Each holds ``home/``, ``work/`` and
  ``uv-python/``.

``list`` prints one line per copy still kept: its key, size, the date it is
deleted, and what it belonged to. ``download`` writes one of those copies, a
gzipped tar, to ``--out`` (default: the key's file name in the current
directory). The bytes come straight from the bucket by a short-lived URL; only
keys ``list`` shows can be fetched. Exits 1 for a key that is not kept.
"""

import argparse
import asyncio
import shutil
import sys
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.storage import private_storage
from app.domain.machine.models import RetainedHomeArchive
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


async def kept_homes(
    conversation: uuid.UUID | None = None, session_id: uuid.UUID | None = None
) -> list[RetainedHomeArchive]:
    query = select(RetainedHomeArchive).order_by(
        RetainedHomeArchive.delete_after, RetainedHomeArchive.key
    )
    if conversation is not None:
        query = query.where(RetainedHomeArchive.conversation_id == conversation)
    if session_id is not None:
        query = query.where(RetainedHomeArchive.session_id == session_id)
    async with async_session_factory() as session:
        return list(await session.scalars(query))


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


async def list_homes(conversation: uuid.UUID | None, session_id: uuid.UUID | None):
    for row in await kept_homes(conversation, session_id):
        print(
            row.key,
            row.size,
            row.delete_after.date().isoformat(),
            f"conversation={row.conversation_id}",
            f"session={row.session_id or '-'}",
            f"pushed={row.published}",
            sep="\t",
        )
    return 0


async def _fetch(key: str, keys: set[str], out: Path | None) -> int:
    if key not in keys:
        print(f"{key} not_kept", file=sys.stderr)
        return 1
    url = await private_storage().presign(key, "get_object", URL_TTL_S)
    target = out or Path(key.rsplit("/", 1)[-1])
    with urllib.request.urlopen(url, timeout=600) as response, target.open("wb") as f:
        shutil.copyfileobj(response, f, 1 << 20)
    print(f"{key} -> {target} ({target.stat().st_size} bytes)")
    return 0


async def download(key: str, out: Path | None) -> int:
    return await _fetch(key, {str(row.key) for row in await kept()}, out)


async def download_home(key: str, out: Path | None) -> int:
    return await _fetch(key, {row.key for row in await kept_homes()}, out)


def main() -> int:
    parser = argparse.ArgumentParser(description="The files kept before deletion.")
    groups = parser.add_subparsers(dest="group", required=True)
    rooms = groups.add_parser("rooms", help="old rooms' working files")
    commands = rooms.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    fetch = commands.add_parser("download")
    fetch.add_argument("key")
    fetch.add_argument("--out", type=Path)
    homes = groups.add_parser("homes", help="archives of cloud sandbox homes")
    home_commands = homes.add_subparsers(dest="command", required=True)
    listing = home_commands.add_parser("list")
    listing.add_argument("--conversation", type=uuid.UUID)
    listing.add_argument("--session", type=uuid.UUID)
    home_fetch = home_commands.add_parser("download")
    home_fetch.add_argument("key")
    home_fetch.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.group == "homes":
        if args.command == "list":
            return asyncio.run(list_homes(args.conversation, args.session))
        return asyncio.run(download_home(args.key, args.out))
    if args.command == "list":
        return asyncio.run(list_kept())
    return asyncio.run(download(args.key, args.out))


if __name__ == "__main__":
    sys.exit(main())
