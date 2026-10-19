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


def _gate(conclusion: str, name: str = "CI required", suite: int = 1) -> Check:
    return Check(
        name=name,
        status="completed",
        conclusion=conclusion,
        url=f"https://ci.example/{name}/{conclusion}",
        suite=suite,
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
    """One batch (ANCHOR, HEAD] with commits B and C in it, red at HEAD.

    HEAD is the commit the gate ran on; B and C are inside the batch and have
    only the cancelled runs their superseded pushes left.
    """
    forge.green = ANCHOR
    forge.ran[ANCHOR] = [_gate("success")]
    # The pushes at B and C were superseded by a newer one in the concurrency
    # group: their gate is cancelled, and only HEAD's ran. A job that finished
    # before the cancellation stays green — it must not be read as the gate's
    # verdict.
    forge.ran[LINE_ONE] = [_gate("cancelled"), _gate("success", "scope")]
    forge.ran[LINE_TWO] = [_gate("cancelled"), _gate("success", "scope")]
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

    # A line whose blamed neighbour has not written its merge commit yet waits
    # a tick before it is sure that neighbour will hear.
    watch(client)
    watch(client)

    told_first = _told(client, first)
    assert "失败文件只有这条线的提交改过" in told_first
    assert "本批共 3 个提交" in told_first
    assert "backend/tests/test_a.py" in told_first
    assert _told(client, second) == told_second, (
        "the other line was told to fix a batch it did not break"
    )
    assert _landing(client, first).checks.value == "failed"
    assert _landing(client, second).checks.value == "preexisting"


def test_failures_that_name_a_commit_nobody_watches_wake_the_lines_that_are(
    client, app_world, forge
):
    """The failed tests point at LINE_TWO's commit, but that merge has no task
    being watched (a push made by hand, say). Staying quiet because the blame
    is 'someone else's' would leave a red batch with nobody told."""
    project = _make_project(client)
    first = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told_before = _told(client, first)
    _red_batch(forge, failing=["backend/tests/test_b.py"])

    watch(client)

    told = _told(client, first)
    assert told != told_before, "a red batch woke nobody"
    assert "各自排除" in told
    assert "没有哪条在看的线能单独认领" in told
    assert _landing(client, first).checks.value == "failed"


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


def test_failures_that_name_two_lines_files_wake_both(client, app_world, forge):
    first, second = _two_lines(client, app_world, forge)
    _red_batch(forge, failing=["backend/tests/test_a.py"])
    # Both lines changed the file the failing test is about: nothing unique.
    forge.changes[LINE_TWO] = CommitChange(LINE_TWO, "feat: a too", ("backend/a.py",))
    forge.batches[(ANCHOR, HEAD)] = BatchCommits(
        total=3, changes=tuple(forge.changes.values()), complete=True
    )

    watch(client)

    for task_id in (first, second):
        told = _told(client, task_id)
        assert "各自排除" in told
        assert "backend/tests/test_a.py" in told
        assert _landing(client, task_id).checks.value == "failed"


def test_a_gate_still_queued_says_nothing(client, app_world, forge):
    """`scope` finishing is not the gate finishing: the product jobs queue."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told = _told(client, task_id)
    forge.ran[LINE_ONE] = [
        _gate("success", "scope"),
        Check("CI required", "queued", None, "https://ci.example/gate"),
    ]
    # A covering answer exists; without the queued rule the look would read
    # this commit as passed through it.
    forge.covered[LINE_ONE] = (LINE_ONE, [_gate("success")])

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "watching"


def test_a_later_green_batch_settles_an_intermediate_commit(client, app_world, forge):
    """A red batch after the green one must not be sheeted home to this
    commit: it landed before the green batch's head and is not in it."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told = _told(client, task_id)
    forge.ran[LINE_ONE] = [_gate("cancelled"), _gate("success", "scope")]
    forge.ran["later"] = [_gate("failure")]
    forge.covered[LINE_ONE] = ("later", forge.ran["later"])
    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD)}

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "passed"


def test_the_batch_is_walked_back_when_main_green_names_nothing(
    client, app_world, forge
):
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=HEAD)
    told = _told(client, task_id)
    forge.green = None
    forge.parents = {HEAD: LINE_TWO, LINE_TWO: LINE_ONE, LINE_ONE: ANCHOR}
    forge.ran[HEAD] = [_gate("failure")]
    forge.ran[LINE_TWO] = [_gate("cancelled")]
    forge.ran[LINE_ONE] = [_gate("cancelled")]
    forge.ran[ANCHOR] = [_gate("success")]
    forge.changes = {
        HEAD: CommitChange(HEAD, "feat: d", ("backend/d.py",)),
        LINE_TWO: CommitChange(LINE_TWO, "feat: b", ("backend/b.py",)),
        LINE_ONE: CommitChange(LINE_ONE, "feat: a", ("backend/a.py",)),
    }
    forge.failing[HEAD] = ["backend/tests/test_d.py"]

    watch(client)

    told_after = _told(client, task_id)
    assert told_after != told
    assert "本批共 3 个提交" in told_after
    assert "只有这条线" in told_after
    assert _landing(client, task_id).checks.value == "failed"


def test_a_commit_a_later_batch_did_not_cover_keeps_its_fix(client, app_world, forge):
    first, second = _two_lines(client, app_world, forge)
    _red_batch(forge, failing=["backend/tests/test_nowhere.py"])
    watch(client)
    assert "各自排除" in _told(client, second)

    forge.ran[HEAD] = [
        _gate("success"),
        _gate("success", "backend tests (unit)"),
    ]
    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD)}  # only the first landed in that batch

    watch(client)

    assert "这条催修撤销" in _told(client, first)
    assert "这条催修撤销" not in _told(client, second)
    assert _landing(client, second).checks.value == "failed"


def test_a_red_check_the_batch_does_not_rerun_is_not_withdrawn(
    client, app_world, forge
):
    """A green Required CI batch says nothing about a red image build."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success")]
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway", suite=2),
    ]
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[HEAD] = [
        _gate("success"),
        _gate("success", "backend tests (unit)"),
        # build.yml runs on every main push, so the green head reports it too;
        # excluding by name would withdraw the build failure with it.
        _gate("success", "build-gateway", suite=2),
    ]
    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD)}

    watch(client)

    assert "这条催修撤销" not in _told(client, task_id)
    assert _landing(client, task_id).checks.value == "failed"


def test_a_later_green_batch_takes_the_blame_back(client, app_world, forge):
    first, second = _two_lines(client, app_world, forge)
    _red_batch(forge, failing=["backend/tests/test_nowhere.py"])
    watch(client)
    assert "各自排除" in _told(client, first)
    assert "各自排除" in _told(client, second)

    forge.ran[HEAD] = [
        _gate("success"),
        _gate("success", "backend tests (unit)"),
    ]
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


def test_a_gate_that_was_rerun_green_is_not_red(client, app_world, forge):
    """A re-run leaves both runs on the commit; the pass is the verdict."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told = _told(client, task_id)
    forge.ran[LINE_ONE] = [
        _gate("failure"),
        _gate("success"),
        _gate("success", "scope"),
    ]

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "passed"


def test_a_batch_blame_with_no_suite_on_its_gate_is_still_withdrawn(
    client, app_world, forge
):
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents[LINE_ONE] = None
    forge.changes[LINE_ONE] = CommitChange(LINE_ONE, "feat: a", ("backend/a.py",))
    forge.failing[LINE_ONE] = ["backend/tests/test_a.py"]
    forge.ran[LINE_ONE] = [
        Check("CI required", "completed", "failure", "u", suite=None)
    ]
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[HEAD] = [_gate("success")]
    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD)}
    watch(client)

    assert "这条催修撤销" in _told(client, task_id)
    assert _landing(client, task_id).checks.value == "preexisting"


def test_an_unsettled_red_is_not_withdrawn(client, app_world, forge):
    """A completed red plus a re-run still going is not a green check yet."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success")]
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway", suite=2),
    ]
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[HEAD] = [_gate("success")]
    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD)}
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway", suite=2),
        Check("build-gateway", "in_progress", None, "u", suite=2),
    ]
    watch(client)

    assert "这条催修撤销" not in _told(client, task_id)
    assert _landing(client, task_id).checks.value == "failed"


def test_a_neutral_gate_is_a_pass(client, app_world, forge):
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told = _told(client, task_id)
    forge.ran[LINE_ONE] = [Check("CI required", "completed", "neutral", "u", suite=1)]

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "passed"


def test_a_neutral_anchor_ends_the_walk_back(client, app_world, forge):
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=HEAD)
    told = _told(client, task_id)
    forge.green = None
    forge.parents = {HEAD: LINE_TWO, LINE_TWO: LINE_ONE, LINE_ONE: ANCHOR}
    forge.ran[HEAD] = [_gate("failure")]
    forge.ran[LINE_TWO] = [_gate("cancelled")]
    forge.ran[LINE_ONE] = [_gate("cancelled")]
    forge.ran[ANCHOR] = [Check("CI required", "completed", "neutral", "u", suite=1)]
    forge.changes = {
        HEAD: CommitChange(HEAD, "feat: d", ("backend/d.py",)),
        LINE_TWO: CommitChange(LINE_TWO, "feat: b", ("backend/b.py",)),
        LINE_ONE: CommitChange(LINE_ONE, "feat: a", ("backend/a.py",)),
        ANCHOR: CommitChange(ANCHOR, "feat: a0", ("backend/a0.py",)),
    }
    forge.failing[HEAD] = ["backend/tests/test_d.py"]

    watch(client)

    told_after = _told(client, task_id)
    assert told_after != told
    assert "本批共 3 个提交" in told_after


def test_a_green_rerun_of_another_workflows_check_settles_it(client, app_world, forge):
    """A red build re-run green on the commit is settled, so the batch going
    green takes the fix back instead of leaving a stale red."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success")]
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway", suite=2),
    ]
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway", suite=2),
        _gate("success", "build-gateway", suite=2),
    ]
    forge.ran[HEAD] = [_gate("success")]
    forge.green = HEAD
    forge.ahead = {(LINE_ONE, HEAD)}
    watch(client)

    assert "这条催修撤销" in _told(client, task_id)
    assert _landing(client, task_id).checks.value == "preexisting"
