"""The main gate runs in batches: a red batch is put on the line that broke it.

The rules, as stated before the code was written:

- One run covers every commit since `main-green`, the last green batch. A
  commit inside a batch has no run of its own — its push was superseded and
  its check runs are cancelled — so the watch reads the run that covers it.
- The failed tests in the red job's log name test files. A commit of the batch
  whose changes are about those files is the one that broke it, when exactly
  one commit matches: only that task's line hears.
- When the failures point at no commit, or at several, every task in the batch
  hears, told how many commits it covers and what went red, and each is asked
  to rule itself out.
- A later batch that goes green takes the blame back: the fix is withdrawn.
- A green batch says nothing.
"""

import uuid

import pytest
from sqlalchemy import select

from app.domain.review.landing_forge import BatchCommits, Check, CommitChange
from app.domain.review.landing_models import TaskLanding
from tests.delivery import delivery_task_id
from tests.integration.test_a_task_delivers_in_steps import _file, _told
from tests.integration.test_accept_pr import (
    _accept,
    _give_card_a_pr,
    _make_project,
    _make_topic,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.integration.test_a_merge_is_watched_on_main import (
    forge as _forge_fixture,
)
from tests.landing import summary_turn_ends, watch

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]
forge = pytest.fixture(_forge_fixture.__wrapped__)  # type: ignore[attr-defined]

ANCHOR = "anchor-a"
LINE_ONE = "commit-b"
LINE_TWO = "commit-c"
HEAD = "commit-d"


def _gate(conclusion: str, name: str = "CI required") -> Check:
    return Check(
        name=name,
        status="completed",
        conclusion=conclusion,
        url=f"https://ci.example/{name}/{conclusion}",
    )


def _task(
    client, app_world, forge, project_id: str, *, pr_number: int, sha: str
) -> str:
    """One task whose merge is ``sha``, in ``project_id``."""
    fake = app_world["fake"]
    topic = _make_topic(client, project_id)
    card = _file(client, topic, completes_task=True)
    head = _give_card_a_pr(client, app_world, topic, card, pr_number)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[pr_number] = sha
    forge.merges[pr_number] = sha
    accepted = _accept(client, card)
    assert accepted.status_code == 200, accepted.text
    task_id = delivery_task_id(client, topic)
    summary_turn_ends(client, task_id)
    return task_id


def _two_lines(client, app_world, forge) -> tuple[str, str]:
    project = _make_project(client)
    first = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    second = _task(client, app_world, forge, project, pr_number=8, sha=LINE_TWO)
    return first, second


def _red_batch(forge, *, failing: list[str]) -> None:
    """One batch (ANCHOR, HEAD] with commits B and C in it, red at HEAD."""
    forge.green = ANCHOR
    forge.ran[ANCHOR] = [_gate("success")]
    # The pushes at B and C were superseded by a newer one in the concurrency
    # group: their runs are cancelled, and only HEAD's ran.
    forge.ran[LINE_ONE] = [_gate("cancelled")]
    forge.ran[LINE_TWO] = [_gate("cancelled")]
    forge.ran[HEAD] = [
        _gate("failure"),
        _gate("failure", "backend tests (unit)"),
    ]
    forge.covered[LINE_ONE] = (HEAD, forge.ran[HEAD])
    forge.covered[LINE_TWO] = (HEAD, forge.ran[HEAD])
    forge.changes = {
        LINE_ONE: CommitChange(LINE_ONE, "feat: a", ("backend/a.py",)),
        LINE_TWO: CommitChange(LINE_TWO, "feat: b", ("backend/b.py",)),
        HEAD: CommitChange(HEAD, "feat: d", ("backend/d.py",)),
    }
    forge.batches[(ANCHOR, HEAD)] = BatchCommits(
        total=3,
        changes=tuple(forge.changes.values()),
        complete=True,
    )
    forge.failing[HEAD] = failing


def _landing(client, task_id: str) -> TaskLanding:
    async def read():
        async with client.test_factory() as session:
            return await session.scalar(
                select(TaskLanding).where(
                    TaskLanding.task_id == uuid.UUID(str(task_id))
                )
            )

    return client.portal.call(read)


def test_failures_that_name_one_lines_files_wake_only_that_line(
    client, app_world, forge
):
    first, second = _two_lines(client, app_world, forge)
    told_second = _told(client, second)
    _red_batch(forge, failing=["backend/tests/test_a.py"])

    watch(client)

    told_first = _told(client, first)
    assert "失败用例所在的文件只有这条线的提交改过" in told_first
    assert "本批共 3 个提交" in told_first
    assert "backend/tests/test_a.py" in told_first
    assert _told(client, second) == told_second, (
        "the other line was told to fix a batch it did not break"
    )
    assert _landing(client, first).checks.value == "failed"
    assert _landing(client, second).checks.value == "preexisting"


def test_failures_that_name_nobody_wake_every_line_in_the_batch(
    client, app_world, forge
):
    first, second = _two_lines(client, app_world, forge)
    _red_batch(forge, failing=["backend/tests/test_nowhere.py"])

    watch(client)

    for task_id in (first, second):
        told = _told(client, task_id)
        assert "本批共 3 个提交" in told
        assert "各自排除" in told
        assert "test_nowhere.py" in told
        assert _landing(client, task_id).checks.value == "failed"


def test_a_later_green_batch_takes_the_blame_back(client, app_world, forge):
    first, second = _two_lines(client, app_world, forge)
    _red_batch(forge, failing=["backend/tests/test_nowhere.py"])
    watch(client)
    assert "各自排除" in _told(client, first)
    assert "各自排除" in _told(client, second)

    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD), (LINE_TWO, HEAD)}
    watch(client)

    for task_id in (first, second):
        told = _told(client, task_id)
        assert told.count("这条催修撤销") == 1
        assert _landing(client, task_id).checks.value == "preexisting"
    # Once is enough: a later tick says nothing more.
    before = _told(client, first)
    watch(client)
    assert _told(client, first) == before


def test_a_red_commit_a_later_green_batch_covered_says_nothing(
    client, app_world, forge
):
    """The watch may first look after the next batch already settled it."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told = _told(client, task_id)
    forge.ran[LINE_ONE] = [_gate("failure")]
    forge.ran[HEAD] = [_gate("success")]
    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD)}

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "passed"


def test_a_green_batch_says_nothing(client, app_world, forge):
    first, second = _two_lines(client, app_world, forge)
    told_first = _told(client, first)
    told_second = _told(client, second)
    forge.green = ANCHOR
    forge.ran[ANCHOR] = [_gate("success")]
    forge.ran[LINE_ONE] = [_gate("cancelled")]
    forge.ran[LINE_TWO] = [_gate("cancelled")]
    forge.ran[HEAD] = [_gate("success")]
    forge.covered[LINE_ONE] = (HEAD, forge.ran[HEAD])
    forge.covered[LINE_TWO] = (HEAD, forge.ran[HEAD])

    watch(client)

    assert _told(client, first) == told_first
    assert _told(client, second) == told_second
    assert _landing(client, first).checks.value == "passed"
    assert _landing(client, second).checks.value == "passed"
