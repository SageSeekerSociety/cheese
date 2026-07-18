"""One-shot migration: memory_entries (DB backend) → embedded OpenViking.

Feeds every existing flat memory fact through the OpenViking write path
(session commit → LLM extraction into the taxonomy), scope by scope. Safe to
re-run: imported entry ids are checkpointed in {openviking_data_dir}/migrated.json
after every batch, so a crash resumes where it left off and nothing is
imported twice. The DB rows are NOT deleted — they stay as the audit trail
(and as the live store while memory_backend is still "db").

Run (from backend/, with .env configured — extraction calls the model):
    PYTHONPATH=. uv run python scripts/migrate_memory_to_openviking.py
    PYTHONPATH=. uv run python scripts/migrate_memory_to_openviking.py --dry-run

Extraction happens in OpenViking background tasks; the script waits for them
before exiting so a green run means the data is actually in.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.domain.memory.models import MemoryEntry
from app.domain.memory.openviking_store import OpenVikingMemoryStore, get_runtime

LOG_DIR = Path("./logs")
STATE_PATH = Path(settings.openviking_data_dir) / "migrated.json"


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("migrate_memory")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (
        logging.FileHandler(LOG_DIR / "migrate_memory_to_openviking.log"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


def _load_state() -> set[str]:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text()))
    return set()


def _save_state(done: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(done), indent=0))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would migrate"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="migrate at most N entries (0 = all)"
    )
    args = parser.parse_args()
    logger = _setup_logging()

    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(MemoryEntry).order_by(MemoryEntry.created_at.asc())
            )
        ).all()
    await engine.dispose()

    done = _load_state()
    todo = [r for r in rows if str(r.id) not in done]
    if args.limit:
        todo = todo[: args.limit]
    logger.info(
        "memory_entries: %d total, %d already migrated, %d to go",
        len(rows),
        len(rows) - len(todo),
        len(todo),
    )
    if args.dry_run:
        for r in todo:
            logger.info(
                "[dry-run] %s/%s %s: %s",
                r.scope.value,
                r.scope_id,
                r.id,
                r.content[:80],
            )
        return 0
    if not todo:
        logger.info("nothing to do")
        return 0

    store = OpenVikingMemoryStore()
    ok = 0
    failed = 0
    for i, r in enumerate(todo, 1):
        t0 = time.monotonic()
        try:
            await store.remember(r.scope, r.scope_id, r.content)
        except Exception as exc:  # noqa: BLE001 - per-item isolation, keep going
            failed += 1
            logger.error(
                "[%d/%d] FAIL %s/%s %s: %s",
                i,
                len(todo),
                r.scope.value,
                r.scope_id,
                r.id,
                exc,
            )
            continue
        done.add(str(r.id))
        _save_state(done)
        ok += 1
        logger.info(
            "[%d/%d] ok %s/%s %s (%.0fms): %s",
            i,
            len(todo),
            r.scope.value,
            r.scope_id,
            r.id,
            (time.monotonic() - t0) * 1000,
            r.content[:80],
        )

    # Extraction runs in OpenViking background tasks — wait so a finished run
    # means the memories are actually written, then shut down cleanly. The
    # global tracker sees all scopes (the client-level API is user-filtered).
    logger.info("waiting for background extraction tasks...")
    await get_runtime().client()  # ensure initialized
    from openviking.service.task_tracker import get_task_tracker

    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        remaining = 0
        for status in ("pending", "running"):
            remaining += len(
                await get_task_tracker().list_tasks(
                    task_type="session_commit", status=status, limit=1000
                )
            )
        if remaining == 0:
            break
        logger.info("  %d extraction task(s) still running...", remaining)
        await asyncio.sleep(5)
    await get_runtime().close()

    logger.info("done: %d migrated, %d failed", ok, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
