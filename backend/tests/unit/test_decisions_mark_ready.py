"""`mark_ready` 换基：翻 draft 之前先把落后的分支挪到当前基线上。

分支 CI 用的是分支 head 上的工作流 —— 一个落后 main 的分支会拿它被切出来那天的
检查集跑一整轮，而合进 main 之后 merge_group 还要拿新的再跑一遍。换 head 只能
发生在人看之前：采纳之后再推提交，平台的合并调用会因为 head 不符 409，并作废
他看过那个 sha 才给的批准。

另一个坑：草稿本身不是冲突。Forgejo 对所有 WIP PR 都报 `mergeable: false`
（它的 draft 就是标题里的 `WIP:`），GitHub 在 PR 转正之前根本不给 mergeable。
按 raw `False` 判冲突，会把每一个草稿都拒在 ready 门外。
"""

import uuid
from dataclasses import replace
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


class _World:
    """Two fakes share one head, and a successful update-branch moves it —
    the way the forge does. GitHub accepts the call with 202 first, so the
    code re-reads until the head it asked to replace is gone.

    `lag` models that background apply: the first read after the call still
    sees the old head, the second sees the new one (and the code waits)."""

    def __init__(self, *, lag: bool = False) -> None:
        self.head = "sha-1"
        self.pending: str | None = None
        self.pending_reads = 0
        self.lag = lag

    def settle(self) -> None:
        """Apply a pending head move — late, when the forge is lagging."""
        if self.pending is None:
            return
        self.pending_reads += 1
        if not self.lag or self.pending_reads > 1:
            self.head, self.pending = self.pending, None


def _status(**fields) -> PullRequestStatus:
    """一张开着的 draft PR —— 线上 GitHub 对它的诚实回答是 mergeable=null、
    mergeable_state="unknown"（2026-10-17 在 #4186 等四张草稿上核过）。"""
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

    def __init__(self, events, world, *, statuses, draft=True, title="fix(x): y"):
        self.events = events
        self.world = world
        self.statuses = list(statuses)
        self.draft = draft
        self.title = title
        self.subjects: list[str] = []

    async def pr_view(self, number):
        self.events.append("pr_view")
        # The head at the time of the read: after a rebase this must still be
        # the commit the task wrote, not the merge commit the update lands.
        return {
            "draft": self.draft,
            "node_id": "PR_node_7",
            "title": self.title,
            "head": {"sha": self.world.head},
        }

    async def pr_status(self, number):
        self.events.append("pr_status")
        self.world.settle()
        template = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return replace(template, head_sha=self.world.head)

    async def commit_subject(self, sha):
        self.events.append("commit_subject")
        self.subjects.append(sha)
        return "fix(x): y"

    async def update_pr(self, number, *, title=None):
        self.events.append("update_pr")
        return {}

    async def mark_ready_for_review(self, node_id):
        self.events.append("mark_ready_for_review")


class _Forge:
    """`_status_client`：按次拿 token 的那只，比较祖先关系、合并基线。"""

    def __init__(
        self, events, world, *, ancestry=("diverged",), updated=True, error=None
    ):
        self.events = events
        self.world = world
        self.ancestry = list(ancestry)
        self.updated = updated
        self.error = error
        self.compare = None
        self.update_calls = []

    async def compare_status(self, *, owner, repo, base, head, token):
        self.events.append("compare_status")
        self.compare = (owner, repo, base, head, token)
        if self.error is not None:
            raise self.error
        return self.ancestry.pop(0) if len(self.ancestry) > 1 else self.ancestry[0]

    async def update_branch(self, *, owner, repo, number, token):
        self.events.append("update_branch")
        self.update_calls.append((number, token))
        if self.updated:
            self.world.pending = f"{self.world.head}-rebased"
        return self.updated


def _task(**fields) -> SimpleNamespace:
    values = {
        "id": TASK,
        "status": TaskStatus.open,
        "branch_name": "topic/abc",
        "pr_number": 7,
        "pr_url": "https://github.com/acme/widgets/pull/7",
        "base_branch": "main",
        "project_id": PROJECT,
        **fields,
    }
    return SimpleNamespace(**values)


async def _ready(
    *, statuses, ancestry=("diverged",), updated=True, error=None, task=None, lag=False
):
    """Call `mark_ready` with every forge side faked; returns what the caller
    sees plus the two fakes and the shared event log."""
    events: list[str] = []
    world = _World(lag=lag)
    publisher = _Publisher(events, world, statuses=statuses)
    forge = _Forge(events, world, ancestry=ancestry, updated=updated, error=error)
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
                require_in_room=AsyncMock(return_value=task or _task())
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
        statuses=[_status()], ancestry=("ahead",)
    )

    assert answer["ready"] is True
    assert forge.update_calls == []
    assert "update_branch" not in events
    assert "mark_ready_for_review" in events


@pytest.mark.anyio
async def test_ready_does_not_flip_a_conflicted_branch():
    """冲突 → 不翻，把原因交回去。翻了也只会让 branch CI 红着等解冲突。"""
    answer, _publisher, forge, events = await _ready(
        statuses=[_status(mergeable_state="dirty")]
    )

    assert answer["ready"] is False
    assert "冲突" in answer["reason"]
    assert forge.update_calls == []
    assert "mark_ready_for_review" not in events


@pytest.mark.anyio
async def test_ready_treats_a_forgejo_draft_as_a_draft_not_a_conflict():
    """Forgejo 的草稿一律 `mergeable: false`（draft 就是 WIP 标题）。

    按 raw mergeable 判冲突，这类项目里每一个草稿都永远翻不了 ready。"""
    answer, _publisher, forge, events = await _ready(
        statuses=[_status(mergeable=False, mergeable_state="draft")],
        ancestry=("ahead",),
    )

    assert answer["ready"] is True
    assert forge.update_calls == []
    assert "mark_ready_for_review" in events


@pytest.mark.anyio
async def test_ready_reports_a_refused_rebase_instead_of_flipping_anyway():
    """换基被拒、且重新读到的仍是落后 + 冲突 → 不翻。"""
    answer, _publisher, forge, events = await _ready(
        statuses=[
            _status(),
            _status(mergeable_state="dirty"),
        ],
        updated=False,
    )

    assert answer["ready"] is False
    assert "冲突" in answer["reason"]
    assert forge.update_calls == [(7, "ghs_write")]
    assert "mark_ready_for_review" not in events
    assert events.count("pr_status") == 2


@pytest.mark.anyio
async def test_ready_waits_for_a_background_update_to_land():
    """GitHub 的 202 是「受理」，head 还没动。翻 ready 前必须等到新 head，
    否则全量 CI 起在旧 head 上、再在新 head 上重跑一遍 —— 又白跑一轮。"""
    answer, _publisher, _forge, events = await _ready(statuses=[_status()], lag=True)

    assert answer["ready"] is True
    # 初始读 + 换基后两次读（第一次仍是旧 head，第二次才动）
    assert events.count("pr_status") == 3
    assert events.index("update_branch") < events.index("mark_ready_for_review")


@pytest.mark.anyio
async def test_ready_does_not_flip_when_the_updated_head_conflicts():
    """换基落地后新 head 撞冲突 → 不翻。"""
    answer, _publisher, _forge, events = await _ready(
        statuses=[_status(), _status(mergeable_state="dirty")],
        lag=True,
    )

    assert answer["ready"] is False
    assert "冲突" in answer["reason"]
    assert "mark_ready_for_review" not in events


@pytest.mark.anyio
async def test_ready_keeps_refusing_when_the_branch_is_still_behind():
    """换基被拒、重读后仍然落后 → 不翻，把「可能撞冲突」说清楚。"""
    answer, _publisher, _forge, events = await _ready(
        statuses=[_status()], ancestry=("diverged", "diverged"), updated=False
    )

    assert answer["ready"] is False
    assert "自动更新分支被拒" in answer["reason"]
    assert "冲突" in answer["reason"]
    assert "mark_ready_for_review" not in events


@pytest.mark.anyio
async def test_ready_flips_when_the_refusal_meant_already_current():
    """GitHub 的 422 也用于「已经是最新的」。这时不该把 ready 卡住：再读一次
    祖先关系，已经追上了就照常翻。"""
    answer, _publisher, _forge, events = await _ready(
        statuses=[_status()],
        ancestry=("diverged", "ahead"),
        updated=False,
    )

    assert answer["ready"] is True
    assert "mark_ready_for_review" in events


@pytest.mark.anyio
async def test_ready_uses_the_default_branch_when_the_task_has_no_base():
    """任务没写 base_branch 时，比较的是项目的默认分支，不是空的 base。"""
    answer, _publisher, forge, _events = await _ready(
        statuses=[_status()], task=_task(base_branch=None)
    )

    assert answer["ready"] is True
    assert forge.compare[2] == "main"


@pytest.mark.anyio
async def test_the_ready_title_reads_the_head_the_task_wrote_before_the_rebase():
    """换基落下的是合并提交，它的标题不是这次工作该落的那一行。

    所以标题必须取自换基之前那次 pr_view 读到的 head。"""
    events: list[str] = []
    world = _World()
    publisher = _Publisher(events, world, statuses=[_status()], title="WIP: task")
    forge = _Forge(events, world)
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

    assert answer["ready"] is True
    assert publisher.subjects == ["sha-1"]  # 不是 "sha-1-rebased"
    assert "update_pr" in events


@pytest.mark.anyio
async def test_a_forge_hiccup_does_not_stop_the_delivery():
    """读不到祖先关系就照旧翻 ready：这里卡一次，交付就少一次。"""
    answer, _publisher, forge, events = await _ready(
        statuses=[_status()], error=RuntimeError("forge down")
    )

    assert answer["ready"] is True
    assert forge.update_calls == []
    assert "mark_ready_for_review" in events
