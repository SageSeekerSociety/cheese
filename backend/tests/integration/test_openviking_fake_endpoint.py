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
    from openviking import AsyncOpenViking
    from openviking.storage import viking_fs
    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfigSingleton,
    )

    from app.domain.memory.openviking_store import get_runtime

    await get_runtime().close()
    await AsyncOpenViking.reset()
    # `reset()` drops the client singleton and nothing else, and two more
    # singletons keep the model clients alive past it — the `openai.AsyncOpenAI`
    # objects built on the (now joined) queue-worker loop, each inside its
    # per-loop client cache. Left in place they stay alive into whatever test
    # runs next; the fixture below says what happens when they finally die.
    #   * `init_viking_fs` stores the VikingFS in a module global that nothing
    #     in openviking ever clears; it holds the embedder. Private surface,
    #     because the package offers no reset for it.
    #   * the config singleton caches the VLM instance on its `VLMConfig`
    #     (`_vlm_instance`); it holds the chat client. The app re-initializes
    #     this singleton from its own conf on every `client()`, so resetting it
    #     here changes nothing for the next test.
    viking_fs._instance = None
    OpenVikingConfigSingleton.reset_instance()


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
    # Collect the embedder's leftover clients HERE, in a sync teardown with no
    # event loop running — not "later, whenever". openviking builds an
    # `openai.AsyncOpenAI` on its queue-worker thread's own loop and caches it
    # by that loop; `_shutdown()` joins the worker but nothing closes the
    # client, so it survives its loop inside a reference cycle until cyclic GC
    # frees it. openai's `AsyncHttpxClientWrapper.__del__` then does
    # `asyncio.get_running_loop().create_task(self.aclose())`: on whatever loop
    # is running at that moment, against a socket bound to the dead worker
    # loop, which raises `Event loop is closed` into whichever unrelated test
    # happens to be running (#693 — the victim was different every time). With
    # no loop running, `get_running_loop()` raises inside that `__del__`, its
    # `except Exception: pass` swallows it, and the sockets are simply dropped.
    # Measured 2026-09-05: 3–4 such clients alive after each test here, 0 after
    # this line; see tmp/queued-input-probe/gc_probe.py.
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
