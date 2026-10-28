"""A task PR with no card yet gets one word when its checks turn green.

Until a card is filed, `poll_open_prs` does not look at the PR at all (it walks
cards), so the green that tells the line it can file one has to come from
`poll_uncarded_task_prs`. These tests drive that poll with a fake forge whose
conclusion the test sets, and a fake room to record what was said, so the
transition — and only the transition — can be asserted from outside.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.review import forge_clients, pr_poll
from app.domain.review.github_pr.task_pr_metadata import TaskPrMetadata
from app.domain.review.github_pr_status import PullRequestStatus
from app.domain.room_task.models import TaskStatus

HEAD = "0123456789abcdef0123456789abcdef01234567"

RUNNING = [{"name": "CI required", "status": "in_progress", "conclusion": None}]
GREEN = [{"name": "CI required", "status": "completed", "conclusion": "success"}]
RED = [{"name": "CI required", "status": "completed", "conclusion": "failure"}]
CANCELLED = [{"name": "CI required", "status": "completed", "conclusion": "cancelled"}]
STALE = [{"name": "CI required", "status": "completed", "conclusion": "stale"}]


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

    async def execute(self, _query):
        row = SimpleNamespace(
            id=self.task.id,
            project_id=self.task.project_id,
            pr_number=self.task.pr_number,
        )
        return SimpleNamespace(all=lambda: [row])

    async def get(self, _model, _ident, **_kwargs):
        return self.task

    async def scalar(self, _query):
        return getattr(self.task, "pending_card", None)

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _Client:
    """每一次 `check_runs` 读下一条答案；读了几次留着给断言看。"""

    head = HEAD

    def __init__(self, answers):
        self._answers = list(answers)
        self.asked: list[str] = []
        self.metadata_calls = []
        self.single_reads = 0

    async def pr_status(self, number):
        self.single_reads += 1
        return PullRequestStatus(head_sha=self.head, state="open", merged=False)

    async def task_pr_metadata(self, numbers):
        self.metadata_calls.append(tuple(numbers))
        return {n: TaskPrMetadata(n, self.head, "open", False) for n in numbers}

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
        status=TaskStatus.open,
        closing_since=None,
        pr_checks_head=None,
        pr_checks_state=None,
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


def test_running_twice_then_green_wakes_the_line_exactly_once(said):
    run, _task = said
    client = _Client([RUNNING, RUNNING, GREEN])
    assert run(client) == []
    assert run(client) == []
    assert run(client) == ["PR #1542 的检查已全绿，可以提交审阅了"]
    # 第四次读到同一个 head 的全绿：没有从 running 过来的那一步，不再说。
    assert run(_Client([GREEN])) == []
    assert client.asked == [HEAD, HEAD, HEAD]
    assert client.metadata_calls == [(1542,), (1542,), (1542,)]
    assert client.single_reads == 0


def test_the_first_green_a_restarted_process_sees_wakes_nobody(said):
    """库里没有记录（新任务、第一次观察）：第一次就看到 green 不是「刚转绿」，
    叫醒等于每张已经绿了的未递卡 PR 都叫一遍。"""
    run, _task = said
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


def test_a_cancelled_run_is_not_a_verdict(said):
    """被顶替或被人工取消的那次运行不是裁决：同一 head 上一轮还在跑、这一轮以
    cancelled 收场，不能说「全绿」。把 cancelled 折回 green，下面第一条断言就红。"""
    run, _task = said
    assert run(_Client([RUNNING])) == []
    # running → cancelled：这次运行没给出结论，不叫。
    assert run(_Client([CANCELLED])) == []
    # 再读到 running 也不叫：上一次不是 running，这次也不是 green。
    assert run(_Client([RUNNING])) == []
    # 真跑到绿才叫一次。
    assert run(_Client([GREEN])) == ["PR #1542 的检查已全绿，可以提交审阅了"]
    # 之后再来一次 cancelled 也不叫（上一轮已是 green，不是 running）。
    assert run(_Client([CANCELLED])) == []


def test_a_pushed_head_starts_over(said):
    """换了 head 只更新记录：旧 head 的 running 不能给新 head 的全绿作保。"""
    run, _task = said

    class _Moved(_Client):
        head = "f" * 40

    assert run(_Client([RUNNING])) == []
    assert run(_Moved([GREEN])) == []
    assert run(_Moved([GREEN])) == []


def test_a_closed_task_is_not_told_to_file_a_card(said):
    """这个轮询的查询刻意留着「已关、PR 还开着」的任务（外面有人把 PR 合了要落
    账）。叫醒那一句是叫线去递卡，对一条已经关掉的任务是错的话，`record_instruction`
    写的行也会被投递端以「任务已不再开放」拒掉。"""
    run, task = said
    assert run(_Client([RUNNING])) == []
    task.status = TaskStatus.closed
    assert run(_Client([GREEN])) == []
    # 记录照常更新：任务重新打开后，同一个 head 再转绿仍不该说（上一次不是 running）。
    assert run(_Client([GREEN])) == []
    task.status = TaskStatus.open
    assert run(_Client([RUNNING])) == []
    assert run(_Client([GREEN])) == ["PR #1542 的检查已全绿，可以提交审阅了"]


def test_a_restart_keeps_the_last_state_so_the_flip_is_not_lost(said):
    """状态落在任务行上：「重启」（轮询器重新跑一遍，进程内什么都不带）之后
    仍从库里的 running 接着判，转绿那一次不丢。把落库改回进程内存，这条就红。"""
    run, task = said
    assert run(_Client([RUNNING])) == []
    assert (task.pr_checks_head, task.pr_checks_state) == (HEAD, "running")
    assert not hasattr(pr_poll, "_uncarded_checks_seen")
    assert run(_Client([GREEN])) == ["PR #1542 的检查已全绿，可以提交审阅了"]
    assert task.pr_checks_state == "green"


def test_a_carded_task_waiting_for_review_is_woken_once(said):
    """已递卡、卡还 pending 的任务也在轮询范围里，转绿说的是「等验收」，
    翻第二次（同一 head 再绿一次）不重复。"""
    run, task = said
    task.pending_card = uuid.uuid4()
    assert run(_Client([RUNNING])) == []
    assert run(_Client([GREEN])) == ["PR #1542 的检查已全绿，待审阅"]
    assert run(_Client([GREEN])) == []


def test_the_poll_query_keeps_tasks_with_a_pending_card():
    """范围只排除已有「非 pending」卡的任务；改回「有卡就排除」，这条红。"""
    import inspect

    source = inspect.getsource(pr_poll.poll_uncarded_task_prs)
    assert "AcceptCard.status != AcceptStatus.pending" in source


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([], "running"),
        (RUNNING, "running"),
        (RED, "red"),
        (GREEN, "green"),
        (GREEN + RUNNING, "running"),
        (GREEN + RED, "red"),
        (
            GREEN + [{"name": "b", "status": "completed", "conclusion": "skipped"}],
            "green",
        ),
        # 没有裁决的 run 不是绿：被顶替的那次以 cancelled 收场，读成全绿就会对着
        # 一个还没人跑完的 head 说「可以递卡了」。
        (
            GREEN + [{"name": "c", "status": "completed", "conclusion": "cancelled"}],
            "running",
        ),
        (CANCELLED, "running"),
        (STALE, "running"),
        (GREEN + STALE, "running"),
    ],
)
def test_the_fold_into_three_words(runs, expected):
    assert pr_poll._checks_state(runs) == expected


def test_a_failed_available_batch_never_selects_the_single_pr_read(said):
    run, _task = said

    class InvalidBatch(_Client):
        async def task_pr_metadata(self, numbers):
            return None

    client = InvalidBatch([])
    assert run(client) == []
    assert client.single_reads == 0 and client.asked == []


def test_a_provider_without_batch_support_keeps_its_existing_read(said):
    run, _task = said
    inner = _Client([RUNNING, GREEN])

    class SinglePrClient:
        async def pr_status(self, number):
            return await inner.pr_status(number)

        async def check_runs(self, ref):
            return await inner.check_runs(ref)

    client = SinglePrClient()
    assert run(client) == []
    assert run(client) == ["PR #1542 的检查已全绿，可以提交审阅了"]
    assert inner.single_reads == 2 and inner.metadata_calls == []


def test_a_pr_replaced_while_checks_wait_cannot_receive_the_old_wake(said):
    run, task = said
    assert run(_Client([RUNNING])) == []

    class ChangedPr(_Client):
        async def check_runs(self, ref):
            result = await super().check_runs(ref)
            task.pr_number = 999
            return result

    assert run(ChangedPr([GREEN])) == []
    assert task.pr_checks_state == "running"


def test_a_task_that_starts_closing_while_checks_wait_is_not_woken(said):
    run, task = said
    assert run(_Client([RUNNING])) == []

    class ClosingTask(_Client):
        async def check_runs(self, ref):
            result = await super().check_runs(ref)
            task.closing_since = datetime.now(UTC)
            return result

    assert run(ClosingTask([GREEN])) == []
