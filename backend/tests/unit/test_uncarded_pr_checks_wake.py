"""A task PR with no card yet gets one word when its checks turn green.

Until a card is filed, `poll_open_prs` does not look at the PR at all (it walks
cards), so the green that tells the line it can file one has to come from
`poll_uncarded_task_prs`. These tests drive that poll with a fake forge whose
conclusion the test sets, and a fake room to record what was said, so the
transition — and only the transition — can be asserted from outside.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.domain.review import forge_clients, pr_poll
from app.domain.review.github_pr_status import PullRequestStatus

HEAD = "0123456789abcdef0123456789abcdef01234567"

RUNNING = [{"name": "CI required", "status": "in_progress", "conclusion": None}]
GREEN = [{"name": "CI required", "status": "completed", "conclusion": "success"}]
RED = [{"name": "CI required", "status": "completed", "conclusion": "failure"}]


class _Sessions:
    """`Chat.session_factory` 的那一次调用：一个会话，从头到尾。"""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        session = self._session

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class _Session:
    def __init__(self, task):
        self.task = task

    async def scalars(self, _query):
        return [self.task.id]

    async def get(self, _model, _ident):
        return self.task

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _Client:
    """每一次 `check_runs` 读下一条答案；读了几次留着给断言看。"""

    def __init__(self, answers):
        self._answers = list(answers)
        self.asked: list[str] = []

    async def pr_status(self, number):
        return PullRequestStatus(head_sha=HEAD, state="open", merged=False)

    async def check_runs(self, ref):
        self.asked.append(ref)
        return self._answers.pop(0)


@pytest.fixture
def said(monkeypatch):
    """跑一轮轮询，返回这一轮说出去的每一句（`record_instruction` 记的）。"""

    task = SimpleNamespace(
        id=uuid.uuid4(),
        pr_number=1542,
        project_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        pr_url="https://github.com/acme/widgets/pull/1542",
    )
    session = _Session(task)
    said: list[str] = []

    async def record(_session, _event, *, task_id, content):
        assert task_id == task.id
        said.append(content)

    async def announce(_session, **kwargs):
        assert kwargs["place_id"] == task.room_id
        assert kwargs["task_id"] == task.id
        assert kwargs["content"] == said[-1]

    async def no_shared_quota(_project, _session):
        return None

    async def never_called(*_args, **_kwargs):
        raise AssertionError("an unmerged PR must not settle the task")

    monkeypatch.setattr(pr_poll, "background_quota", no_shared_quota)
    pr_poll._uncarded_checks_seen.clear()

    def run(client):
        async def fake_client(_project, _session):
            return client

        monkeypatch.setattr(forge_clients, "proposal_client", fake_client)
        before = len(said)
        asyncio.run(
            pr_poll.poll_uncarded_task_prs(
                SimpleNamespace(session_factory=_Sessions(session)),
                announce,
                task.project_id,
                record_instruction=record,
                task_teammate=never_called,
            )
        )
        return said[before:]

    yield run, task
    pr_poll._uncarded_checks_seen.clear()


def test_running_twice_then_green_wakes_the_line_exactly_once(said):
    run, _task = said
    client = _Client([RUNNING, RUNNING, GREEN])
    assert run(client) == []
    assert run(client) == []
    assert run(client) == ["PR #1542 的检查已全绿，可以递卡了"]
    # 第四次读到同一个 head 的全绿：没有从 running 过来的那一步，不再说。
    assert run(_Client([GREEN])) == []
    assert client.asked == [HEAD, HEAD, HEAD]


def test_the_first_green_a_restarted_process_sees_wakes_nobody(said):
    """重启后字典是空的：第一次就看到 green 不是「刚转绿」，叫醒等于每次部署
    把每张已经绿了的未递卡 PR 都叫一遍。"""
    run, _task = said
    pr_poll._uncarded_checks_seen.clear()
    assert run(_Client([GREEN])) == []
    # 再来一次全绿也不叫：上一轮已经不是 running 了。
    assert run(_Client([GREEN])) == []


def test_green_after_red_is_not_the_step_this_says(said):
    """只认 running → green。红过一轮再绿，等它的那件事已经换了（重跑），
    不该按「刚跑完」去催递卡。"""
    run, _task = said
    client = _Client([RUNNING, RED, GREEN, GREEN])
    assert run(client) == []
    assert run(client) == []
    assert run(client) == []
    assert run(client) == []


def test_a_pushed_head_starts_over(said):
    """换了 head 只更新记录：旧 head 的 running 不能给新 head 的全绿作保。"""
    run, _task = said

    class _Moved(_Client):
        async def pr_status(self, number):
            return PullRequestStatus(
                head_sha="f" * 40, state="open", merged=False
            )

    assert run(_Client([RUNNING])) == []
    assert run(_Moved([GREEN])) == []
    assert run(_Moved([GREEN])) == []


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([], "running"),
        (RUNNING, "running"),
        (RED, "red"),
        (GREEN, "green"),
        (GREEN + RUNNING, "running"),
        (GREEN + RED, "red"),
        (GREEN + [{"name": "b", "status": "completed", "conclusion": "skipped"}], "green"),
        (GREEN + [{"name": "c", "status": "completed", "conclusion": "cancelled"}], "green"),
    ],
)
def test_the_fold_into_three_words(runs, expected):
    assert pr_poll._checks_state(runs) == expected
