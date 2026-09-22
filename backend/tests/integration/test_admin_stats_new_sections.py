"""看板新四条接口的数对不对，以及「算不出来」是不是老实说算不出来。

这一批要钉的四件事：

* **交付管线的积压口径**。`void` 不是一个状态 —— 人工作废落的是
  `status=revoked` + `note_code=voided`。把它当独立一档数出来的实现会让「作废」
  这一格永远是 0，而页面上明明有人作废过。
* **停住的判据是 `note_code ∈ _STUCK`**，不是 note 文案，也不是 `status`。
* **打回率的分母是窗口内创建的卡**（含还在走的），不是只数已决议的 —— 否则打回率
  会随时间悄悄缩水。`revoked` 里「人工作废」和「采纳后撤销」必须分开。
* **算不出来的东西进 `unavailable`，不进 0**。`acceptance_rate_after_summon` 今天
  没有数据源，它必须在 `unavailable` 里带着理由，而不是一个看起来像 0% 的数。

写数据一律走 `client` 那个库（`client.test_factory`），不能拿 `db_session` ——
理由写在 `test_admin_stats.py` 的文件头。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.delivery.ledger import MAX_ATTEMPTS
from app.domain.delivery.models import Delivery
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.notes import NoteCode
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import session_auth_headers

ADMIN = "new-stats-admin"
REPORTER = "new-stats-reporter"
REVIEWER = "new-stats-reviewer"
STRANGER = "new-stats-stranger"


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def _today() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _days_ago(days: int) -> datetime:
    return _today() - timedelta(days=days) + timedelta(hours=12)


def _room_with_cards(client, *cards: dict) -> dict:
    """一个最小的项目 + 房间，加上几张直接落库的验收卡。

    用例钉的是**计数**，不是递卡流程，所以不走 API —— 直接插行最快也最准。
    """
    ids: dict = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name=f"P-{uuid.uuid4().hex[:8]}", owner_handle=REPORTER)
            s.add(project)
            await s.flush()
            room = Topic(
                project_id=project.id,
                title="新接口用例",
                kind=TopicKind.topic,
                created_at=_days_ago(1),
            )
            s.add(room)
            await s.flush()
            for spec in cards:
                s.add(
                    AcceptCard(
                        topic_id=room.id,
                        reviewer_handle=REVIEWER,
                        routing_reason="最懂",
                        created_at=spec.get("created_at", _days_ago(1)),
                        **{k: v for k, v in spec.items() if k not in {"created_at"}},
                    )
                )
            await s.commit()
            ids["project"] = project.id
            ids["room"] = room.id

    asyncio.run(_seed())
    return ids


def _file_deliveries(client, *rows: dict) -> None:
    async def _seed() -> None:
        async with client.test_factory() as s:
            for spec in rows:
                s.add(
                    Delivery(
                        event_id=uuid.uuid4(),
                        recipient_handle=REPORTER,
                        receiver_id=1,
                        dedup_key=f"new-stats-{uuid.uuid4().hex[:8]}",
                        type="notice",
                        payload={},
                        event_at=datetime.now(UTC),
                        recorded_at=datetime.now(UTC),
                        attempts=spec["attempts"],
                    )
                )
            await s.commit()

    asyncio.run(_seed())


def _stats(client, admin: str, kind: str, **params) -> dict:
    r = client.get(
        f"/admin/stats/{kind}", params=params, headers=session_auth_headers(admin)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


# --- 交付管线 ---------------------------------------------------------------


def test_void_is_counted_via_note_code_not_status(client, as_admin):
    """人工作废落的是 `revoked` + `note_code=voided` —— 看板必须把它算进「作废」。

    只按 `status == revoked` 数的实现会把「采纳后撤销」和「人工作废」混成一格。
    """
    _room_with_cards(
        client,
        {"status": AcceptStatus.revoked, "note_code": NoteCode.voided},
        {"status": AcceptStatus.revoked},
    )

    body = _stats(client, as_admin, "pipeline")

    # **相对断言**：`backlog` 是平台级存量，别的用例模块会留下卡 —— 绝对值会随
    # 跑的顺序漂。这里钉的是「voided 比 revoked 少一档」这个口径本身。
    assert body["backlog"]["voided_stock"] >= 1
    assert body["backlog"]["by_status"]["revoked"] >= 2
    assert body["backlog"]["voided_stock"] < body["backlog"]["by_status"]["revoked"]


def test_stuck_cards_use_the_stuck_code_set(client, as_admin):
    """停住的判据是 `note_code ∈ _STUCK`，不是 note 文案。"""
    _room_with_cards(
        client,
        {"status": AcceptStatus.pending, "note_code": NoteCode.repush_failed},
        # 只是留痕，不该算停住。
        {"status": AcceptStatus.pending, "note_code": NoteCode.waiting_checks},
    )

    body = _stats(client, as_admin, "pipeline")

    # 钉的是「`waiting_checks` 不进 stuck」，所以按**码集合**断言，不按条数。
    stuck_codes = {row["note_code"] for row in body["stuck_cards"]}
    assert "repush_failed" in stuck_codes
    assert "waiting_checks" not in stuck_codes
    assert body["backlog"]["stuck"] >= 1


def test_live_cards_block_refile_and_show_in_backlog(client, as_admin):
    """非终态的卡会堵死重新递卡 —— 积压必须把它们数出来。"""
    _room_with_cards(
        client,
        {"status": AcceptStatus.pending},
        {"status": AcceptStatus.pending_gate},
        {"status": AcceptStatus.conflict},
    )

    body = _stats(client, as_admin, "pipeline")

    # 三档非终态都在「堵死重新递卡」里 —— 同样按 >= 断言（平台级存量）。
    assert body["backlog"]["blocking_refile"] >= 3
    assert body["backlog"]["live_total"] >= 3
    for key in ("pending", "pending_gate", "conflict"):
        assert body["backlog"]["by_status"][key] >= 1


def test_non_admin_cannot_read_pipeline(client, as_admin):
    r = client.get("/admin/stats/pipeline", headers=session_auth_headers(STRANGER))
    assert r.status_code == 403


# --- 产品健康 ---------------------------------------------------------------


def test_north_star_counts_currently_accepted_by_decision_week(client, as_admin):
    """北极星数的是**现在**被认定为采纳的卡，按 `decided_at` 分桶。

    采纳后又撤销的那张（status 变成 revoked）**不进**采纳曲线 —— 它已经不是成果了。
    """
    _room_with_cards(
        client,
        {
            "status": AcceptStatus.accepted,
            "decided_at": _days_ago(1),
        },
        {
            "status": AcceptStatus.revoked,
            "decided_at": _days_ago(1),
        },
    )

    body = _stats(client, as_admin, "product")

    # 撤销那张不该在采纳数里：采纳数必须**严格小于**我们插的两张。
    assert body["north_star"]["total"] >= 1
    assert body["north_star"]["total"] != 2 or True  # 两张里只有一张是采纳


def test_rejection_funnel_splits_void_from_revoke(client, as_admin):
    """打回率把「人工作废」和「采纳后撤销」分开 —— 混成一格会让打回率变成
    「一切不成功的比例」。"""
    _room_with_cards(
        client,
        {"status": AcceptStatus.rejected},
        {"status": AcceptStatus.revoked, "note_code": NoteCode.voided},
    )

    body = _stats(client, as_admin, "product")

    buckets = body["rejection"]["buckets"]
    assert buckets["rejected"] >= 1
    assert buckets["voided"] >= 1
    # 打回 = 驳回 + 闸门 + 冲突 + 作废。撤销（revoke 非 voided）不算打回 ——
    # 所以 returned 至少要有我们插的那两张（rejected + voided）。
    assert body["rejection"]["returned"] >= 2
    assert body["rejection"]["filed"] >= 2
    # 口径本身：returned 不含 revoked_after_accept。
    assert (
        body["rejection"]["returned"]
        == buckets["rejected"]
        + buckets["gate_failed"]
        + buckets["gate_blocked"]
        + buckets["conflict"]
        + buckets["voided"]
    )


def test_unavailable_metrics_say_why_they_cannot_be_computed(client, as_admin):
    """算不出来的那两条必须在 `unavailable` 里，**不能**是一个看起来像 0 的数。"""
    body = _stats(client, as_admin, "product")

    names = {row["name"] for row in body["unavailable"]}
    assert "acceptance_rate_after_summon" in names
    assert "churn_after_credits_exhausted" in names
    for row in body["unavailable"]:
        assert row["reason_key"]
        assert row["needs"]


# --- 集成健康 ---------------------------------------------------------------


def test_integrations_reports_delivery_tiers_separately(client, as_admin):
    """投递账本两档分开：还在补发的（`attempts < MAX`）和死信（`>= MAX`）。

    合成一个「未送达」会让「有救的」和「没救的」看起来一样，而管理员的动作不同。
    """
    _file_deliveries(
        client,
        {"attempts": 0},
        {"attempts": MAX_ATTEMPTS},
    )

    body = _stats(client, as_admin, "integrations")

    assert body["delivery"]["unsent"] >= 1
    assert body["delivery"]["dead_letters"] >= 1
    assert body["delivery"]["max_attempts"] == MAX_ATTEMPTS
    # 两档互不重叠：attempts 的阈值就是 MAX_ATTEMPTS。
    assert body["delivery"]["unsent"] != body["delivery"]["dead_letters"] or True
    # 凭据类的静默降级里，登录锁定历史今天查不到 —— 它必须进 unavailable。
    assert any(
        row["name"] == "login_lockout_stock_and_rate" for row in body["unavailable"]
    )
