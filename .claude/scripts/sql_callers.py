"""Which line of our own code issues each of a request's statements?

``sql_census.py`` says how many statements a request makes and what each
touches; ``split_topic_cpu.py`` says what each await costs the loop. Neither
says *who asked for it*: with 28 statements a request, the useful question is
whether two of them are the same question asked twice, or whether one page
re-reads a set it already has.

This hangs the same listener off ``before_cursor_execute`` and records, for each
statement, the innermost frame under ``backend/app`` on the stack — that is the
repository or service line that issued it, not ``AsyncSession.execute``.

Output, per endpoint:

  * one line per **distinct statement text**: calls per request, rows brought
    back per request, and the app frames that issue it. Two frames on one
    statement mean two places ask the same question;
  * then, in ``PERF_BY_CALLER=1`` mode, one line per **call site**: every
    statement that frame issues, so a call site that fires five statements is
    visible as one row instead of five.

One request per endpoint, sequential: with one request in flight the statement
order is the request's own, and the driver's per-cursor timer is that
statement's own wait.

    PERF_DB_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf \\
    PERF_REDIS_URL=redis://127.0.0.1:6379/0 \\
        uv run --project backend python .claude/scripts/sql_callers.py

``PERF_ONLY`` filters endpoint labels, as in ``sql_census.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from contextvars import ContextVar
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
ONLY = [s.strip() for s in os.environ.get("PERF_ONLY", "").split(",") if s.strip()]
BY_CALLER = bool(os.environ.get("PERF_BY_CALLER", "").strip())
APP_DIR = REPO / "backend" / "app"

os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET", "perf-probe-secret")
os.environ.setdefault(
    "REDIS_URL", os.environ.get("PERF_REDIS_URL", "redis://127.0.0.1:6379/0")
)

sys.path.insert(0, str(REPO / ".claude" / "scripts"))
from concurrent_read_probe import discover_ids, endpoint_paths  # noqa: E402

#: The frames we drop before naming a call site: the driver's own plumbing is
#: between `before_cursor_execute` and our code, and naming it tells a reader
#: nothing about which of our functions asked for the query.
_SKIP = ("site-packages", "/.venv/", "/lib/python", "<frozen", "/asyncio/")

#: The app frame that is currently awaiting SQLAlchemy. It cannot be read off
#: the traceback inside the cursor listener: SQLAlchemy's async adapter runs the
#: sync session in a greenlet, so by then our own frames are on the *parent*
#: greenlet and the listener only sees the adapter. So the site is taken where
#: the await is made, in this task's context, and the listener reads it back.
_SITE: ContextVar[str] = ContextVar("sql_site", default="(unset)")


def caller() -> str:
    """The innermost frame under ``backend/app``, as ``path:line function``."""
    for frame in reversed(traceback.extract_stack()):
        if not frame.filename.startswith(str(APP_DIR)):
            continue
        if any(s in frame.filename for s in _SKIP):
            continue
        rel = frame.filename[len(str(REPO / "backend")) + 1 :]
        return f"{rel}:{frame.lineno} {frame.name}"
    return "(outside backend/app)"


def install(tally: list) -> None:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.db import engine

    def before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("_t0", []).append((time.perf_counter(), _SITE.get()))

    def after(conn, cursor, statement, parameters, context, executemany):
        t0, site = conn.info["_t0"].pop()
        tally.append(
            (statement, (time.perf_counter() - t0) * 1000, cursor.rowcount, site)
        )

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    event.listen(engine.sync_engine, "after_cursor_execute", after)

    for name in ("execute", "scalars", "get", "flush", "stream"):
        original = getattr(AsyncSession, name, None)
        if original is None or not asyncio.iscoroutinefunction(original):
            continue

        async def wrapper(_self, *args, _o=original, _n=name, **kwargs):
            token = _SITE.set(caller())
            try:
                return await _o(_self, *args, **kwargs)
            finally:
                _SITE.reset(token)

        wrapper.__name__ = f"traced_{name}"
        setattr(AsyncSession, name, wrapper)


def flat(statement: str) -> str:
    return re.sub(r"\s+", " ", statement).strip()


def report_by_statement(label: str, tally: list) -> None:
    grouped: dict[str, list] = defaultdict(list)
    for statement, ms, rows, site in tally:
        grouped[flat(statement)].append((ms, rows, site))
    print(f"\n== {label}: {len(tally)} statements, {len(grouped)} distinct ==")
    ordered = sorted(grouped.items(), key=lambda kv: -sum(m for m, _r, _s in kv[1]))
    for text, calls in ordered:
        rows = sum(r for _m, r, _s in calls)
        sites = defaultdict(int)
        for _m, _r, site in calls:
            sites[site] += 1
        where = ", ".join(f"{site} x{n}" for site, n in sites.items())
        print(f"  x{len(calls):<2} rows={rows:<5} {text[:64]}")
        print(f"        {where}")


def report_by_caller(label: str, tally: list) -> None:
    grouped: dict[str, list] = defaultdict(list)
    for statement, ms, rows, site in tally:
        grouped[site].append((flat(statement), rows))
    print(f"\n== {label}: by call site, {len(grouped)} sites ==")
    ordered = sorted(grouped.items(), key=lambda kv: -len(kv[1]))
    for site, calls in ordered:
        print(f"  {len(calls):>3} statements  {site}")
        counts = defaultdict(int)
        for text, _rows in calls:
            counts[text] += 1
        for text, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"        x{n}  {text[:72]}")


async def main() -> None:
    import httpx

    from app.common.auth import create_access_token
    from app.main import app

    tally: list = []
    install(tally)
    ids = await discover_ids()
    targets = [e for e in endpoint_paths(ids) if not ONLY or any(s in e[0] for s in ONLY)]
    token = create_access_token(None, handle=VIEWER)
    transport = httpx.ASGITransport(app=app)
    load = Path("/proc/loadavg").read_text().strip()
    print(f"ids {json.dumps(ids)} requests=1 loadavg={load}")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://probe", timeout=600.0
    ) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        for label, path in targets:
            await client.get(path)  # warm
            tally.clear()
            r = await client.get(path)
            if r.status_code != 200:
                print(f"\n{label}: HTTP {r.status_code} {r.text[:120]}")
                continue
            if BY_CALLER:
                report_by_caller(label, tally)
            else:
                report_by_statement(label, tally)


asyncio.run(main())
