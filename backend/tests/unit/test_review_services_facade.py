"""门面守卫：``app.domain.review.services`` 拆成包之后，公开面一个都不能少。

拆包时把 ``services.py`` 拆成了 ``services/`` 包，类的方法体和模块级名字都搬进了
子模块。调用方（19 个 import 它的文件、还有测试里直接 ``service._record_task_nudge
= AsyncMock()`` 的地方）完全没改，靠的就是 ``services/__init__.py`` 这个门面把同一个
名字空间重新摊开。

这道测试不连数据库，只钉住三件事：

1. 门面该导出的名字都导得出来（包括带下划线的内部名字——它们也是公开面的一部分）。
2. ``AcceptService`` 上的 88 个方法一个不少，而且是**真方法**（类自己的 ``__dict__``
   里就有），不是 ``__getattr__`` 之类动态转发的。
3. ``AcceptService.__init__`` 还在，并且仍然给 ``self._session/_repo/_topics/
   _projects`` 四个属性赋值——这是拆包时明确**没有**动的那部分。
"""

from __future__ import annotations

import inspect

from app.domain.review.services import (
    _CARD_BLOCKS_NEW_CARD,
    _DELIVERABLE_MAX_BYTES,
    _NUDGE_TAIL_LIMIT,
    AcceptService,
    ReviewerAdmission,
    TaskService,
    _force_merge_verdict,
    _github_enforces,
    async_session_factory,
)
from app.domain.review.services import (
    GATE_ABANDONED_PREFIX as GATE_ABANDONED_PREFIX,
)

# 88 个方法，照搬运之前那份 services.py 里 AcceptService 的类体抄的。
# 顺序按定义先后；__init__ 也在内。
ACCEPT_SERVICE_METHODS = [
    "__init__",
    "_topic_or_404",
    "_stamp_delivery",
    "_card_or_404",
    "_reviewer_or_project_default",
    "_require_reviewer_in_room",
    "create_card",
    "_announce_filed",
    "_announce_new_artifact",
    "_warn_about_a_second_pending_migration",
    "project_id_for_topic",
    "mark_gate_started",
    "finish_gate",
    "list_for_topic",
    "open_pr_card_ids",
    "anybody_still_waiting",
    "latest_decision_at",
    "reviewer_topic_ids",
    "describe",
    "_enforce_protocol",
    "reassign",
    "_forbid_ai",
    "approve",
    "arm_auto_merge",
    "_notify_merge_result",
    "_seen_head",
    "_seen_head_or_refresh",
    "_refresh_never_shown_card",
    "_refresh_github_unseen_head",
    "accept",
    "_accept_github",
    "_accept_discussion",
    "_pr_repo_of",
    "_status_client",
    "_dependency_block_reason",
    "_sync_dependency_target",
    "_sync_task_dependency_target",
    "_pr_verdict",
    "_mirror_pr_verdict",
    "_write_merge_mirror",
    "_merge_pr_for_accept",
    "_record_queue_entry",
    "_conclude_pr_accept",
    "_refresh_stale_card",
    "_pr_poll_credentials",
    "_app_credentials",
    "advance_pr_card",
    "refresh_stale_pr_snapshots",
    "_merge_snapshot_age_s",
    "_refresh_stale_pr_snapshot",
    "_refresh_github_snapshot",
    "_advance_github_card",
    "_mark_task_merged",
    "_app_pr_client",
    "mark_ready",
    "redescribe",
    "push_fix",
    "note_poll_crashed",
    "_note_poll_failed",
    "_poll_pr_card",
    "_update_behind_branch",
    "_dismiss_stale_accept",
    "_tell_the_reviewer",
    "_notify_ready",
    "_required_absence_overdue",
    "_merge_armed_card",
    "_note_needs_human",
    "_settle_external_merge",
    "_void_closed_pr_card",
    "_record_task_nudge",
    "_note_merge_blocked",
    "_ci_nudge",
    "_review_nudge",
    "_conflict_nudge",
    "_dispatch_nudges",
    "_finish_pr_accept",
    "_resolve_forge",
    "_stop_accept_pr_unavailable",
    "_stop_accept_no_branch",
    "_publish_pr_for_accept",
    "_note_outside_accept_txn",
    "merge_queued_pr",
    "_cancel_queued_accept",
    "reject",
    "revoke",
    "merge_despite_checks",
    "_override_github_checks",
    "void",
]


def test_facade_reexports_the_public_names() -> None:
    """上面那些 import 本身就是要断言的东西：少一个这文件就收集不起来。"""
    assert GATE_ABANDONED_PREFIX
    assert isinstance(_DELIVERABLE_MAX_BYTES, int)
    assert callable(_force_merge_verdict)
    assert callable(_github_enforces)
    assert isinstance(_NUDGE_TAIL_LIMIT, int)
    assert isinstance(_CARD_BLOCKS_NEW_CARD, tuple) and _CARD_BLOCKS_NEW_CARD
    assert async_session_factory is not None
    assert ReviewerAdmission is not None
    assert TaskService is not None


def test_accept_service_keeps_all_88_methods() -> None:
    """88 个方法一个不少，而且都是类自己定义的。"""
    assert len(ACCEPT_SERVICE_METHODS) == 88
    found = {name for name, _ in inspect.getmembers(AcceptService)}
    missing = [name for name in ACCEPT_SERVICE_METHODS if name not in found]
    assert missing == []

    # 真方法：类自己的 __dict__ 里就有，不是动态转发来的。
    own = set(vars(AcceptService))
    not_own = [name for name in ACCEPT_SERVICE_METHODS if name not in own]
    assert not_own == []
    assert "__getattr__" not in own


def test_init_still_builds_the_sessions_and_repositories() -> None:
    """拆包没动 __init__：四个属性仍在，且仍从这里赋值。"""
    source = inspect.getsource(AcceptService.__init__)
    for attribute in ("_session", "_repo", "_topics", "_projects"):
        assert attribute in source, f"{attribute} 没在 __init__ 里赋值"
