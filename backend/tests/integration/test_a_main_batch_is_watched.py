"""The main gate runs in batches: a red batch is put on the line that broke it.

The rules, as stated before the code was written:

- One run covers every commit since the last one that ran it. A commit inside
  a batch has no run of the batch's checks — its push was superseded before
  the run started, which on GitHub leaves no check runs at all — so the watch
  reads the run that covers it: the first later commit that ran those checks.
  The batch reaches back to the commit before it that ran them. Other
  workflows (an image build on every push) run on every commit as usual.
- The failed tests in the red job's log name test files. A commit of the batch
  whose changes are about those files is the one that broke it, when exactly
  one commit matches: only that task's line hears.
- When the failures point at no commit, or at several, every task in the batch
  hears, told how many commits it covers and what went red, and each is asked
  to rule itself out.
- A later run that goes green takes the blame back: the fix is withdrawn. A
  later run that is red again does not.
- A green batch says nothing.
"""

import uuid

import pytest
from sqlalchemy import select

from app.domain.review import landing_watch
from app.domain.review.landing_forge import Check, CommitChange
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
NEXT = "commit-e"


def _build() -> Check:
    """The image build, which runs on every push to main."""
    return _gate("success", "build")


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
    """One batch (ANCHOR, HEAD] with commits B and C in it, red at HEAD.

    HEAD is the commit the gate ran on; B and C are inside the batch.
    """
    forge.line = [ANCHOR, LINE_ONE, LINE_TWO, HEAD]
    forge.parents = {HEAD: LINE_TWO, LINE_TWO: LINE_ONE, LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [
        _gate("success"),
        _gate("success", "backend tests (unit)"),
        _build(),
    ]
    # The pushes at B and C were superseded in the queue before their run
    # started: no check runs of it at all, only the build that runs on every
    # push, which went green and must not be read as the batch's verdict.
    forge.ran[LINE_ONE] = [_build()]
    forge.ran[LINE_TWO] = [_build()]
    forge.ran[HEAD] = [
        _gate("failure"),
        _gate("failure", "backend tests (unit)"),
        _build(),
    ]
    forge.changes = {
        LINE_ONE: CommitChange(LINE_ONE, "feat: a", ("backend/a.py",)),
        LINE_TWO: CommitChange(LINE_TWO, "feat: b", ("backend/b.py",)),
        HEAD: CommitChange(HEAD, "feat: d", ("backend/d.py",)),
    }
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
    forge.line = [LINE_ONE, HEAD]
    forge.ran[HEAD] = [_gate("success")]

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "watching"


def test_a_later_green_batch_settles_an_intermediate_commit(client, app_world, forge):
    """A red batch after the green one must not be sheeted home to this
    commit: it landed before the green batch's head and is not in it."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told = _told(client, task_id)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success"), _build()]
    forge.ran[LINE_ONE] = [_build()]
    forge.ran[HEAD] = [_gate("success"), _build()]
    forge.ran["later"] = [_gate("failure"), _build()]
    forge.line = [ANCHOR, LINE_ONE, HEAD, "later"]

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "passed"


def test_the_batch_reaches_back_to_the_last_green_run(client, app_world, forge):
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=HEAD)
    told = _told(client, task_id)
    forge.parents = {HEAD: LINE_TWO, LINE_TWO: LINE_ONE, LINE_ONE: ANCHOR}
    forge.ran[HEAD] = [_gate("failure"), _build()]
    forge.ran[LINE_TWO] = [_build()]
    forge.ran[LINE_ONE] = []
    forge.ran[ANCHOR] = [_gate("success"), _build()]
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


def test_a_later_batch_that_is_red_again_keeps_the_fix(client, app_world, forge):
    first, second = _two_lines(client, app_world, forge)
    _red_batch(forge, failing=["backend/tests/test_nowhere.py"])
    watch(client)
    assert "各自排除" in _told(client, second)

    forge.ran[NEXT] = [_gate("failure"), _build()]
    forge.line.append(NEXT)

    watch(client)

    for task_id in (first, second):
        assert "这条催修撤销" not in _told(client, task_id)
        assert _landing(client, task_id).checks.value == "failed"


def test_a_red_check_the_batch_does_not_rerun_is_not_withdrawn(
    client, app_world, forge
):
    """A green Required CI batch says nothing about a red image build."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success"), _gate("success", "build-gateway")]
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway"),
    ]
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[HEAD] = [
        _gate("success"),
        _gate("success", "backend tests (unit)"),
        # build.yml runs on every main push, so the green head reports it too;
        # it says nothing about the build of the merge commit.
        _gate("success", "build-gateway"),
    ]
    forge.line = [LINE_ONE, HEAD]

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
    # LINE_ONE ended a batch: the push before it was superseded.
    forge.parents = {LINE_ONE: LINE_TWO, LINE_TWO: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success")]
    forge.ran[LINE_TWO] = []
    forge.ran[LINE_ONE] = [_gate("failure")]
    forge.ran[HEAD] = [_gate("success")]
    forge.line = [ANCHOR, LINE_TWO, LINE_ONE, HEAD]

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "passed"


def test_a_green_batch_says_nothing(client, app_world, forge):
    first, second = _two_lines(client, app_world, forge)
    told_first = _told(client, first)
    told_second = _told(client, second)
    forge.line = [ANCHOR, LINE_ONE, LINE_TWO, HEAD]
    forge.parents = {HEAD: LINE_TWO, LINE_TWO: LINE_ONE, LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success")]
    forge.ran[LINE_ONE] = []
    forge.ran[LINE_TWO] = []
    forge.ran[HEAD] = [_gate("success")]

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


def test_an_unsettled_red_is_not_withdrawn(client, app_world, forge):
    """A completed red plus a re-run still going is not a green check yet."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success"), _gate("success", "build-gateway")]
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway"),
    ]
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[HEAD] = [_gate("success")]
    forge.line = [LINE_ONE, HEAD]
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway"),
        Check("build-gateway", "in_progress", None, "u"),
    ]
    watch(client)

    assert "这条催修撤销" not in _told(client, task_id)
    assert _landing(client, task_id).checks.value == "failed"


def test_a_neutral_gate_is_a_pass(client, app_world, forge):
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    told = _told(client, task_id)
    forge.ran[LINE_ONE] = [Check("CI required", "completed", "neutral", "u")]

    watch(client)

    assert _told(client, task_id) == told
    assert _landing(client, task_id).checks.value == "passed"


def test_a_neutral_anchor_ends_the_walk_back(client, app_world, forge):
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=HEAD)
    told = _told(client, task_id)
    forge.parents = {HEAD: LINE_TWO, LINE_TWO: LINE_ONE, LINE_ONE: ANCHOR}
    forge.ran[HEAD] = [_gate("failure")]
    forge.ran[LINE_TWO] = []
    forge.ran[LINE_ONE] = []
    forge.ran[ANCHOR] = [Check("CI required", "completed", "neutral", "u")]
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
    """A red build re-run green on the merge commit is settled: the fix is
    taken back instead of left standing over a stale red."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success"), _gate("success", "build-gateway")]
    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway"),
    ]
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[LINE_ONE] = [
        _gate("success"),
        _gate("failure", "build-gateway"),
        _gate("success", "build-gateway"),
    ]
    watch(client)

    assert "这条催修撤销" in _told(client, task_id)
    assert _landing(client, task_id).checks.value == "preexisting"


def test_a_batch_end_that_went_red_is_withdrawn_once_a_later_batch_is_green(
    client, app_world, forge
):
    """The merge that ended the batch is the one the red run is on; its push
    before it was superseded and left no runs. A later batch going green over
    it settles that red."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=HEAD)
    _red_batch(forge, failing=["backend/tests/test_d.py"])
    watch(client)
    assert _landing(client, task_id).checks.value == "failed"

    forge.ran[NEXT] = [
        _gate("success"),
        _gate("success", "backend tests (unit)"),
        _build(),
    ]
    forge.line.append(NEXT)
    watch(client)

    assert "这条催修撤销" in _told(client, task_id)
    assert _landing(client, task_id).checks.value == "preexisting"


def test_a_merge_whose_batch_is_still_queued_waits_for_it(client, app_world, forge):
    """The build ran on the merge and passed; the batch's run is queued behind
    another and has no runs yet. The merge is not judged on the build alone."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=LINE_ONE)
    forge.parents = {LINE_ONE: ANCHOR}
    forge.ran[ANCHOR] = [_gate("success"), _build()]
    forge.ran[LINE_ONE] = [_build()]
    forge.line = [ANCHOR, LINE_ONE]

    watch(client)

    assert _landing(client, task_id).checks.value == "watching"


def test_a_batch_longer_than_is_read_blames_nobody(client, app_world, forge):
    """Past `BATCH_COMMITS` the batch is not read whole: its failing tests may
    name one commit of what was read, but nothing is pinned on it."""
    project = _make_project(client)
    task_id = _task(client, app_world, forge, project, pr_number=7, sha=HEAD)
    told = _told(client, task_id)
    skipped = [f"skipped-{i}" for i in range(landing_watch.BATCH_COMMITS + 1)]
    forge.parents = {HEAD: skipped[0]}
    for newer, older in zip(skipped, skipped[1:], strict=False):
        forge.parents[newer] = older
    forge.parents[skipped[-1]] = ANCHOR
    for sha in skipped:
        forge.ran[sha] = []
        forge.changes[sha] = CommitChange(sha, f"feat: {sha}", (f"backend/{sha}.py",))
    forge.ran[ANCHOR] = [_gate("success")]
    forge.ran[HEAD] = [_gate("failure")]
    forge.changes[HEAD] = CommitChange(HEAD, "feat: d", ("backend/d.py",))
    forge.failing[HEAD] = ["backend/tests/test_d.py"]

    watch(client)

    told_after = _told(client, task_id)
    assert told_after != told
    assert "各自排除" in told_after
    assert "只有这条线" not in told_after
