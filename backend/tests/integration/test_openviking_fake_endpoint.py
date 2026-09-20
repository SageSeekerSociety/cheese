"""OpenViking backend round trip with NO API key — the key-less counterpart of
``test_openviking_store.py``.

That test proves the real thing and needs a real Zhipu key. This one proves the
*wiring*: ov.conf generation, the embedded AGFS under ``openviking_data_dir``,
extraction, the vector index, scope isolation, and survival across a restart —
all against a local OpenAI-compatible stand-in (``tests.support
.fake_model_endpoint``). It needs no key and no network, so it runs by default
and guards the openviking path from silently rotting while the key is pending.

What it deliberately does NOT prove: extraction quality and semantic
generalization. The stand-in embedder is lexical, so search is asserted on
queries that share wording with the memory. Judging whether the extractor
picked the right facts is the real model's job.
"""

import asyncio
import gc
import time
import uuid

import pytest

from app.core.config import settings
from app.domain.memory.models import MemoryScope
from tests.support.fake_model_endpoint import fake_model_server

pytestmark = pytest.mark.anyio

# The stand-in is instant, so extraction only has to outlast local bookkeeping.
_EXTRACTION_TIMEOUT_S = 120.0


async def _wait_extractions(timeout: float = _EXTRACTION_TIMEOUT_S) -> None:
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
        await asyncio.sleep(0.5)
    raise TimeoutError("extraction tasks did not finish in time")


async def _shutdown() -> None:
    from app.domain.memory.openviking_store import get_runtime

    # The runtime's close() undoes everything client() set up, including the
    # two openviking singletons its own reset() leaves behind — see there.
    await get_runtime().close()


@pytest.fixture
def openviking_on_fake_endpoint(tmp_path, monkeypatch):
    """Point every OpenViking setting at the local stand-in for one test."""
    with fake_model_server() as server:
        monkeypatch.setattr(settings, "openviking_data_dir", str(tmp_path / "viking"))
        monkeypatch.setattr(settings, "openviking_llm_api_base", server["base_url"])
        monkeypatch.setattr(
            settings, "openviking_embedding_api_base", server["base_url"]
        )
        monkeypatch.setattr(settings, "openviking_llm_api_key", "sk-fake-llm")
        monkeypatch.setattr(
            settings, "openviking_embedding_api_key", "sk-fake-embedding"
        )
        monkeypatch.setattr(settings, "openviking_llm_model", "fake-chat")
        monkeypatch.setattr(settings, "openviking_embedding_model", "fake-embedding")
        # Smaller than the 2048 production default purely for speed; the wiring
        # under test is dimension-agnostic.
        monkeypatch.setattr(settings, "openviking_embedding_dimension", 256)
        yield server
    # Collect the model clients HERE, in a sync teardown with no event loop
    # running — not "later, whenever". `_shutdown()` has made them unreferenced
    # (see `_OpenVikingRuntime.close`), but they sit in reference cycles, so
    # only cyclic GC frees them, and openai's `AsyncHttpxClientWrapper.__del__`
    # then does `asyncio.get_running_loop().create_task(self.aclose())` against
    # a socket bound to the queue worker's dead loop. Inside a later test that
    # is `Event loop is closed` landing on that test (#693 — a different victim
    # every time). With no loop running, `get_running_loop()` raises inside the
    # `__del__`, its `except Exception: pass` swallows it, and the sockets are
    # simply dropped. Measured 2026-09-05 with tmp/queued-input-probe/gc_probe.py:
    # 3 and 7 such clients alive after these two tests on main, 0 after this.
    gc.collect()


async def test_openviking_round_trip_without_a_key(openviking_on_fake_endpoint):
    from app.domain.memory.openviking_store import (
        OpenVikingMemoryStore,
        memories_uri,
    )

    server = openviking_on_fake_endpoint
    store = OpenVikingMemoryStore()
    project_id = str(uuid.uuid4())
    other_project = str(uuid.uuid4())

    try:
        # cheese remember → extraction → a card in this scope's taxonomy
        await store.remember(
            MemoryScope.project,
            project_id,
            "本项目技术选型确定：后端 FastAPI，数据库 PostgreSQL，前端 Vue 3。",
        )
        await _wait_extractions()
        # 知识沉淀是副产品: the end-of-turn auto-extract path, same scope
        await store.ingest_turn(
            MemoryScope.project,
            project_id,
            conversation_key="topic-fake-1",
            exchanges=[
                ("user", "演示日定在 7 月 15 号，由 andyl 负责准备演示环境。"),
                ("assistant", "收到：7 月 15 号演示，演示环境 andyl 负责。"),
            ],
        )
        await _wait_extractions()

        lines = await store.recall(MemoryScope.project, project_id)
        assert lines, "extraction produced no recallable memories"
        recalled = "\n".join(lines)
        assert "PostgreSQL" in recalled
        assert "演示" in recalled
        assert await store.count(MemoryScope.project, project_id) == len(lines)

        # A different project's space is empty — scopes are separate user spaces.
        assert await store.recall(MemoryScope.project, other_project) == []

        # The vector index answers, and only with URIs inside this scope.
        hits = await store.search(MemoryScope.project, project_id, "FastAPI")
        assert hits
        prefix = memories_uri(MemoryScope.project, project_id)
        assert all(hit.uri.startswith(prefix) for hit in hits)

        # L2 read, then 人工修剪
        top = hits[0]
        full = await store.read_memory(MemoryScope.project, project_id, top.uri)
        assert full.strip()
        await store.forget(MemoryScope.project, project_id, top.uri)
        left = await store.list_entries(MemoryScope.project, project_id)
        assert all(entry["uri"] != top.uri for entry in left)

        # ov.conf really drove the provider: our configured models were called
        # on both endpoints, so nothing silently fell back to a default.
        kinds = {call["kind"] for call in server["calls"]}
        assert kinds == {"chat", "embeddings"}
        models = {call["body"].get("model") for call in server["calls"]}
        assert models == {"fake-chat", "fake-embedding"}
    finally:
        await _shutdown()


async def test_memories_survive_a_restart(openviking_on_fake_endpoint):
    """The reason ``.viking`` has to be a volume: state lives in that directory
    and nowhere else, so a process restart on the same directory keeps it — and
    a restart on a fresh directory loses everything."""
    from app.domain.memory.openviking_store import OpenVikingMemoryStore

    project_id = str(uuid.uuid4())
    try:
        await OpenVikingMemoryStore().remember(
            MemoryScope.project, project_id, "部署机器是 dev-box，磁盘 500G。"
        )
        await _wait_extractions()
        before = await OpenVikingMemoryStore().recall(MemoryScope.project, project_id)
        assert before

        # Tear the embedded instance down the way a container restart would.
        await _shutdown()

        after = await OpenVikingMemoryStore().recall(MemoryScope.project, project_id)
        assert after == before
    finally:
        await _shutdown()
