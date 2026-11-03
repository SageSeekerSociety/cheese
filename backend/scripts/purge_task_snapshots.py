"""Delete the task snapshot bundles left in the private bucket.

The platform no longer uploads task snapshots (#5071, then the table and its
routes were removed). The objects under ``task-snapshots/`` are storage that
nothing reads again, and the owner decided not to export them first.

Run once, after the release that dropped the table is deployed, from the
backend image on the deployment host:

    python -m scripts.purge_task_snapshots           # list and count only
    python -m scripts.purge_task_snapshots --apply   # delete

It is idempotent: a second run finds nothing left and prints zero. The bucket
prefix is listed page by page, so a large backlog does not have to fit in
memory.
"""

import argparse
import asyncio

from app.core.storage import private_storage

#: Where ``room_task/snapshots`` used to put every bundle. The trailing slash
#: keeps the purge off any future key that merely starts with the same words.
PREFIX = "task-snapshots/"


async def purge(*, apply: bool) -> int:
    bucket = private_storage()
    seen = 0
    deleted = 0
    async for key in bucket.list_keys(PREFIX):
        seen += 1
        if apply and await bucket.delete(key):
            deleted += 1
    if apply:
        print(f"found {seen} object(s) under {PREFIX}; deleted {deleted}")
    else:
        print(f"found {seen} object(s) under {PREFIX}; nothing deleted (pass --apply)")
    return deleted


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the listed objects; without it the run only counts them",
    )
    args = parser.parse_args()
    await purge(apply=args.apply)


if __name__ == "__main__":
    asyncio.run(main())
