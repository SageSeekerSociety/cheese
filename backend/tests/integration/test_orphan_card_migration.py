"""存量孤儿卡的清理（迁移 b8e1d4c70a92）—— 功能测试.

线上有 12 张卡停在已归档话题上（6 张 pending、6 张 pr_open，最久的挂了 374
小时）。代码修复只挡住"以后不再产生"，这条数据迁移负责把已经存在的收干净。

测试跑的是**迁移里那段真实 SQL**（`close_orphan_cards`，从迁移模块导入），
不是照抄一份——不然测的就不是要发布的东西了。
"""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.topic.models import Topic, TopicKind, TopicStatus

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b8e1d4c70a92_close_orphan_accept_cards.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_orphan_sweep", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_closes_every_stranded_card_and_leaves_the_rest_alone(client):
    """一次性铺出线上那批卡的全部形状，跑一遍 sweep，前后各核对一次。"""
    sweep = _load_migration().close_orphan_cards
    ids: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()

            long_ago = datetime.now(UTC) - timedelta(hours=374)

            def _topic(title: str, *, archived: bool) -> Topic:
                t = Topic(
                    project_id=project.id,
                    title=title,
                    kind=TopicKind.topic,
                    status=(TopicStatus.archived if archived else TopicStatus.active),
                    archived_at=long_ago if archived else None,
                )
                s.add(t)
                return t

            seeded: dict[str, AcceptCard] = {}

            def _card(topic: Topic, key: str, **kw) -> None:
                kw.setdefault("note", "")
                card = AcceptCard(topic_id=topic.id, reviewer_handle="alice", **kw)
                s.add(card)
                seeded[key] = card

            # --- 该被收掉的：已归档话题上的非终态卡 -----------------------
            arch = _topic("已归档-pending", archived=True)
            arch2 = _topic("已归档-第一阶段PR", archived=True)
            arch3 = _topic("已归档-第二阶段PR", archived=True)
            arch4 = _topic("已归档-闸门中", archived=True)
            arch5 = _topic("已归档-冲突", archived=True)
            arch6 = _topic("已归档-已作废闸门", archived=True)
            # --- 不该被碰的 ------------------------------------------------
            live = _topic("还活着-pending", archived=False)
            live2 = _topic("还活着-PR在跑", archived=False)
            await s.flush()

            _card(arch, "pending_archived", status=AcceptStatus.pending)
            _card(
                arch2,
                "pr_stage1",
                status=AcceptStatus.pr_open,
                decided_by="alice",
                decided_at=long_ago,
                pr_number=201,
                pr_url="https://github.com/acme/w/pull/201",
                pr_repo="acme/w",
                pr_head_sha="sha201",
            )
            _card(
                arch3,
                "pr_stage2",
                status=AcceptStatus.pr_open,
                decided_by="alice",
                decided_at=long_ago,
                pr_number=202,
                pr_url="https://github.com/acme/w/pull/202",
                pr_repo="acme/w",
                pr_head_sha="sha202",
                pr_merged_at=long_ago,
                note="PR #202 检查全绿，已自动合并，等部署也成功后才归档。",
            )
            _card(arch4, "gate_archived", status=AcceptStatus.pending_gate)
            _card(arch5, "conflict_archived", status=AcceptStatus.conflict)
            _card(arch6, "gate_failed_archived", status=AcceptStatus.gate_failed)
            _card(live, "pending_live", status=AcceptStatus.pending)
            _card(
                live2,
                "pr_live",
                status=AcceptStatus.pr_open,
                decided_by="alice",
                pr_number=203,
                pr_repo="acme/w",
                pr_head_sha="sha203",
            )
            await s.flush()  # ids are assigned on INSERT, not on add()
            ids.update({k: c.id for k, c in seeded.items()})
            await s.commit()

    asyncio.run(_seed())

    async def _run() -> dict:
        async with client.test_factory() as s:
            result = await s.run_sync(lambda conn: sweep(conn))
            await s.commit()
            return result

    report = asyncio.run(_run())

    # 处理前的核对：5 张孤儿卡（gate_failed 已是终态，不算）。
    assert report["before"] == {
        "conflict": 1,
        "pending": 1,
        "pending_gate": 1,
        "pr_open": 2,
    }
    assert report["settled_merged_pr"] == 1
    assert report["revoked_open_pr"] == 1
    assert report["revoked_undecided"] == 3
    # 处理后的核对：一张不剩。
    assert report["after"] == {}

    async def _check() -> dict[str, AcceptCard]:
        async with client.test_factory() as s:
            return {key: await s.get(AcceptCard, cid) for key, cid in ids.items()}  # type: ignore[misc]

    cards = asyncio.run(_check())

    # 第二阶段（已合并）→ 收尾成 accepted，原 note 保留在后面。
    assert cards["pr_stage2"].status == AcceptStatus.accepted
    assert cards["pr_stage2"].note.startswith("📦 话题归档收尾：PR #202 已合并")
    assert "等部署也成功后才归档" in cards["pr_stage2"].note

    # 第一阶段（PR 还开着）→ revoked，且必须把 PR 链接留在卡上给人处理。
    assert cards["pr_stage1"].status == AcceptStatus.revoked
    assert "https://github.com/acme/w/pull/201" in cards["pr_stage1"].note
    assert "未合并" in cards["pr_stage1"].note
    # 授权来源不被改写。
    assert cards["pr_stage1"].decided_by == "alice"

    # note 里必须留下卡原来停在哪个状态（UPDATE 的 SET 表达式读的是旧行）——
    # 不然事后没人分得清这张 revoked 是人点的还是归档收的。
    for key, was in (
        ("pending_archived", "pending"),
        ("gate_archived", "pending_gate"),
        ("conflict_archived", "conflict"),
    ):
        assert cards[key].status == AcceptStatus.revoked, key
        assert cards[key].note == f"📦 话题归档，验收卡随之关闭（原状态：{was}）。", key

    # 终态卡、活话题上的卡：一个字都不能动。
    assert cards["gate_failed_archived"].status == AcceptStatus.gate_failed
    assert cards["gate_failed_archived"].note == ""
    assert cards["pending_live"].status == AcceptStatus.pending
    assert cards["pending_live"].note == ""
    assert cards["pr_live"].status == AcceptStatus.pr_open
    assert cards["pr_live"].note == ""

    # 幂等：再跑一遍什么都不匹配。
    again = asyncio.run(_run())
    assert again["before"] == {}
    assert (
        again["settled_merged_pr"]
        == again["revoked_open_pr"]
        == again["revoked_undecided"]
        == 0
    )
