"""记忆分层 against real storage: a core fact keeps its seat in every prompt, an
ordinary one has to be about the turn.

The unit tests cover what injection does with a ranking. This covers the parts
only Postgres and the real keyword scorer can show: that the layer survives a
write at all (a field that never round-trips is the same bug as not having it),
and that a fact written weeks ago still beats thirty newer ones when the turn is
about it — which is the whole reason the newest-N rule had to go.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.models import MemoryLayer, MemoryScope
from app.domain.memory.store import DbMemoryStore, recall_pools

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal


def _project(client) -> str:
    r = client.post("/projects", json={"name": "Mem layers"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _pool() -> str:
    return str(uuid.uuid4())


# --- the write side (`cheese remember --core`) ----------------------------


def test_the_layer_survives_a_write_and_is_visible_to_humans(client):
    """The memory page is where a person prunes what the agent remembers, so it
    has to show which entries are the ones costing a seat every turn."""
    pid = _project(client)
    r = client.post(
        f"/projects/{pid}/memory",
        json={"content": "你是芝士，说人话，别堆术语", "layer": "core"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["layer"] == "core"
    client.post(f"/projects/{pid}/memory", json={"content": "沙箱里不要跑 jj"})

    items = client.get(f"/memory?project_id={pid}").json()["data"]["data"]
    layers = {e["content"]: e["layer"] for e in items}
    assert layers["你是芝士，说人话，别堆术语"] == "core"
    # Nothing is born with a permanent seat — it has to be asked for.
    assert layers["沙箱里不要跑 jj"] == "fact"


def test_an_unknown_layer_is_refused_rather_than_stored_as_a_fact(client):
    """Silently downgrading a typo'd layer would put an entry the writer thinks
    is permanent into the pool that gets retrieved on demand."""
    pid = _project(client)
    r = client.post(f"/projects/{pid}/memory", json={"content": "x", "layer": "L0"})
    assert r.status_code == 422


# --- the read side, against real rows -------------------------------------


def test_core_comes_back_whole_and_never_competes_as_a_fact(
    db_session: AsyncSession, _portal: "BlockingPortal"
):
    async def _run() -> None:
        store = DbMemoryStore(db_session)
        pool = _pool()
        await store.remember(
            MemoryScope.project, pool, "你是芝士", layer=MemoryLayer.core
        )
        await store.remember(
            MemoryScope.project, pool, "回答先给结论", layer=MemoryLayer.core
        )
        for fact in ("部署脚本在 deploy.sh", "前端构建用 pnpm"):
            await store.remember(MemoryScope.project, pool, fact)

        assert await store.recall_core(MemoryScope.project, pool) == [
            "你是芝士",
            "回答先给结论",
        ]
        ranked = await store.rank_facts(MemoryScope.project, pool, "你是芝士吗")
        assert [content for _, content in ranked] == [
            "前端构建用 pnpm",
            "部署脚本在 deploy.sh",
        ]
        assert await store.count(MemoryScope.project, pool) == 4

    _portal.call(_run)


def test_the_oldest_fact_wins_the_seat_when_the_turn_is_about_it(
    db_session: AsyncSession, _portal: "BlockingPortal"
):
    async def _run() -> None:
        store = DbMemoryStore(db_session)
        pool = _pool()
        answer = "新增 alembic 迁移后必须把 backend/alembic/HEAD 改成你的 revision id"
        await store.remember(MemoryScope.project, pool, answer)
        for i in range(30):
            await store.remember(MemoryScope.project, pool, f"昨天顺手记的第 {i} 条")

        got = await recall_pools(
            store,
            [(MemoryScope.project, pool)],
            query="我加了一个 alembic 迁移，HEAD 冲突了怎么办",
            char_budget=120,
        )

        # Under the old newest-N rule this fact was the first one dropped.
        assert got.facts[0] == answer
        assert got.omitted == 31 - len(got.facts)

    _portal.call(_run)


def test_core_survives_a_pool_that_has_outgrown_the_prompt(
    db_session: AsyncSession, _portal: "BlockingPortal"
):
    """155 facts at this project's real median length do not fit any sane
    prompt. What must never depend on that is the core layer."""

    async def _run() -> None:
        store = DbMemoryStore(db_session)
        pool = _pool()
        await store.remember(
            MemoryScope.project, pool, "你是芝士，说人话", layer=MemoryLayer.core
        )
        for i in range(155):
            await store.remember(
                MemoryScope.project, pool, f"第 {i} 条事实：" + "细节" * 110
            )

        got = await recall_pools(
            store, [(MemoryScope.project, pool)], query="这一轮在聊部署"
        )

        assert got.facts[0] == "你是芝士，说人话"
        assert got.core_count == 1
        assert got.core_omitted == 0
        # The pool does not fit, and the prompt is told exactly how much of it
        # did not — that is the part that must never go silent.
        assert got.omitted == 156 - len(got.facts)
        assert got.omitted > 0

    _portal.call(_run)
