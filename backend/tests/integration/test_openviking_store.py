"""OpenViking backend round trip — REAL embedded instance, REAL model calls.

Opt-in: needs network + the OpenAI-compatible chat/embedding endpoints from
settings (extraction costs a few model calls and ~30-60s), so it only runs
with CHEESEX_OPENVIKING_TEST=1. It is deliberately NOT mocked — a green run
means remember → extraction → recall/search/forget actually works end to end.

    CHEESEX_OPENVIKING_TEST=1 uv run pytest \
        tests/integration/test_openviking_store.py -q
"""

import asyncio
import os
import time
import uuid

import pytest

from app.core.config import settings
from app.domain.memory.models import MemoryScope

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("CHEESEX_OPENVIKING_TEST") != "1",
        reason=(
            "real OpenViking + model endpoints; set CHEESEX_OPENVIKING_TEST=1 "
            "(requires network and a configured API key)"
        ),
    ),
    pytest.mark.anyio,
]


async def _wait_extractions(timeout: float = 300.0) -> None:
    """Block until every background session_commit extraction task settles."""
    from openviking.service.task_tracker import get_task_tracker

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = 0
        for status in ("pending", "running"):
            remaining += len(
                await get_task_tracker().list_tasks(
                    task_type="session_commit", status=status, limit=100
                )
            )
        if remaining == 0:
            return
        await asyncio.sleep(2)
    raise TimeoutError("extraction tasks did not finish in time")


async def test_openviking_round_trip(tmp_path, monkeypatch):
    if not (settings.openviking_llm_api_key or settings.anthropic_auth_token):
        pytest.skip("no API key configured for OpenViking model endpoints")
    monkeypatch.setattr(settings, "openviking_data_dir", str(tmp_path / "viking"))

    from app.domain.memory.openviking_store import (
        OpenVikingMemoryStore,
        get_runtime,
        memories_uri,
    )

    store = OpenVikingMemoryStore()
    project_id = str(uuid.uuid4())
    other_project = str(uuid.uuid4())

    try:
        # remember → background extraction into the scope's taxonomy
        await store.remember(
            MemoryScope.project,
            project_id,
            "本项目技术选型确定：后端 FastAPI，数据库 PostgreSQL，前端 Vue 3。",
        )
        # turn ingestion (the auto-extract path) into the same scope
        await store.ingest_turn(
            MemoryScope.project,
            project_id,
            conversation_key="topic-test-1",
            exchanges=[
                ("user", "我们决定演示日定在 7 月 15 号，由 andyl 负责准备演示环境。"),
                ("assistant", "收到：7 月 15 号演示，演示环境 andyl 负责。"),
            ],
        )
        await _wait_extractions()

        # recall: L0 abstract lines for this scope only
        lines = await store.recall(MemoryScope.project, project_id)
        assert lines, "extraction produced no recallable memories"
        assert any("PostgreSQL" in ln or "FastAPI" in ln for ln in lines) or any(
            "演示" in ln for ln in lines
        )

        # isolation: a different project's space is empty
        assert await store.recall(MemoryScope.project, other_project) == []

        # semantic search returns hits with viking:// uris inside this scope
        hits = await store.search(MemoryScope.project, project_id, "数据库选型")
        assert hits
        prefix = memories_uri(MemoryScope.project, project_id)
        assert all(h.uri.startswith(prefix) for h in hits)

        # L2 read + forget
        top = hits[0]
        full = await store.read_memory(MemoryScope.project, project_id, top.uri)
        assert full.strip()
        await store.forget(MemoryScope.project, project_id, top.uri)
        left = await store.list_entries(MemoryScope.project, project_id)
        assert all(e["uri"] != top.uri for e in left)
    finally:
        await get_runtime().close()
        from openviking import AsyncOpenViking

        await AsyncOpenViking.reset()
