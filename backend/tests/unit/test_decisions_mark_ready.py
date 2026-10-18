"""`mark_ready` 换基：翻 draft 之前先把落后的分支挪到当前基线上。

分支 CI 用的是分支 head 上的工作流 —— 一个落后 main 的分支会拿它被切出来那天的
检查集跑一整轮，而合进 main 之后 merge_group 还要拿新的再跑一遍。换 head 只能
发生在人看之前：采纳之后再推提交，平台的合并调用会因为 head 不符 409，并作废
他看过那个 sha 才给的批准。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.review.github_pr_status import PullRequestStatus
from app.domain.review.services import decisions
from app.domain.review.services._shared import _GitHubCredentials
from app.domain.review.services.service import AcceptService
from app.domain.room_task.models import TaskStatus
from app.domain.topic.models import TopicStatus

ROOM = uuid.uuid4()
TASK = uuid.uuid4()
PROJECT = uuid.uuid4()


def _status(**fields) -> PullRequestStatus:
    """一张开着的 draft PR —— 线上 GitHub 对它的诚实回答是 mergeable=null、
    mergeable_state="unknown"（2026-10-17 在 #4186 等四张草稿上核过），
    不是 dirty。"""
    values = {
        "head_sha": "sha-1",
        "head_ref": "topic/abc",
        "base_ref": "main",
        "state": "open",
        "merged": False,
        "mergeable": None,
        "mergeable_state": "unknown",
        "draft": True,
        **fields,
    }
    return PullRequestStatus(**values)


class _Publisher:
    """`_app_pr_client` 发下来的那只（App token，repo 绑定）：读视图、读状态、
    翻 ready。每次调用都记进共享的事件表，用例才说得出「换基发生在翻 ready 之前」。"""

    def __init__(self, events, *, statuses, draft=True):
        self.events = events
        self.statuses = list(statuses)
        self.draft = draft

    async def pr_view(self, number):
        self.events.append("pr_view")
        return {"draft": self.draft, "node_id": "PR_node_7", "title": "fix(x): y"}

    async def pr_status(self, number):
        self.events.append("pr_status")
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    async def update_pr(self, number, *, title=None):
        self.events.append("update_pr")
        return {}

    async def mark_ready_for_review(self, node_id):
        self.events.append("mark_ready_for_review")


class _Forge:
    """`_status_client`：按次拿 token 的那只，比较祖先关系、合并基线。"""

    def __init__(self, events, *, ancestry="diverged", updated=True):
        self.events = events
        self.ancestry = ancestry
        self.updated = updated
        self.compare = None
        self.update_calls = []

    async def compare_status(self, *, owner, repo, base, head, token):
        self.events.append("compare_status")
        self.compare = (owner, repo, base, head, token)
        return self.ancestry

    async def update_branch(self, *, owner, repo, number, token):
        self.events.append("update_branch")
        self.update_calls.append((number, token))
        return self.updated


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id=TASK,
        status=TaskStatus.open,
        branch_name="topic/abc",
        pr_number=7,
        pr_url="https://github.com/acme/widgets/pull/7",
        base_branch="main",
        project_id=PROJECT,
    )


async def _ready(*, statuses, ancestry="diverged", updated=True):
    """Call `mark_ready` with every forge side faked; returns what the caller
    sees plus the two fakes and the shared event log."""
    events: list[str] = []
    publisher = _Publisher(events, statuses=statuses)
    forge = _Forge(events, ancestry=ancestry, updated=updated)
    service = AcceptService(MagicMock())
    topic = SimpleNamespace(id=ROOM, project_id=PROJECT, status=TopicStatus.active)
    service._topic_or_404 = AsyncMock(return_value=topic)
    service._app_pr_client = AsyncMock(return_value=publisher)
    service._app_credentials = AsyncMock(
        return_value=(_GitHubCredentials(write="ghs_write", read="ghs_read"), "")
    )
    service._status_client = AsyncMock(return_value=forge)
    binding = SimpleNamespace(repo="acme/widgets", kind="github_app")

    with (
        patch.object(
            decisions,
            "TaskService",
            lambda _session: SimpleNamespace(
                require_in_room=AsyncMock(return_value=_task())
            ),
        ),
        patch(
            "app.domain.project.forge.binding_for_project",
            AsyncMock(return_value=binding),
        ),
        patch(
            "app.domain.project.forge.default_branch",
            AsyncMock(return_value="main"),
        ),
    ):
        answer = await decisions.mark_ready(service, ROOM, TASK)
    return answer, publisher, forge, events


@pytest.mark.anyio
async def test_ready_rebases_a_behind_branch_before_it_flips_the_draft():
    """落后 → 先 update_branch（一次，用写 token），再翻 ready。

    这就是整件事的重点：翻转之后才开始的 CI 读的是换过基的 head，用的是 main
    今天的工作流。顺序反了，这一轮白跑。"""
    answer, _publisher, forge, events = await _ready(statuses=[_status()])

    assert answer["ready"] is True
    assert forge.update_calls == [(7, "ghs_write")]
    assert forge.compare == ("acme", "widgets", "main", "sha-1", "ghs_read")
    assert events.index("update_branch") < events.index("mark_ready_for_review")
    assert events.count("update_branch") == 1


@pytest.mark.anyio
async def test_ready_leaves_an_up_to_date_branch_alone():
    """不落后 → 一次 update_branch 都不打；换基会白改 head，白作废一轮 CI。"""
    answer, _publisher, forge, events = await _ready(
        statuses=[_status()], ancestry="ahead"
    )

    assert answer["ready"] is True
    assert forge.update_calls == []
    assert "update_branch" not in events
    assert "mark_ready_for_review" in events


@pytest.mark.anyio
async def test_ready_does_not_flip_a_conflicted_branch():
    """冲突 → 不翻，把原因交回去。翻了也只会让 branch CI 红着等解冲突。"""
    answer, _publisher, forge, events = await _ready(
        statuses=[_status(mergeable=False, mergeable_state="dirty")]
    )

    assert answer["ready"] is False
    assert "冲突" in answer["reason"]
    assert forge.update_calls == []
    assert "mark_ready_for_review" not in events


@pytest.mark.anyio
async def test_ready_reports_a_refused_rebase_instead_of_flipping_anyway():
    """换基被拒（例如调用途中撞上别人刚推的提交）→ 也不翻：此刻的 head 还是
    落后的那一个，翻过去等于让这轮 CI 白跑。"""
    answer, _publisher, forge, events = await _ready(
        statuses=[
            _status(),
            _status(mergeable=False, mergeable_state="dirty"),
        ],
        updated=False,
    )

    assert answer["ready"] is False
    assert "冲突" in answer["reason"]
    assert forge.update_calls == [(7, "ghs_write")]
    assert "mark_ready_for_review" not in events
    assert events.count("pr_status") == 2
