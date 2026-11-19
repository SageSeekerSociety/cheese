"""A card whose merge state the forge is still working out settles in seconds.

Both GitHub and Forgejo work out whether a PR's head merges after the head
moves, and answer "unknown" until they have. The poller asks again a moment
later rather than leaving the card to the next tick, a minute away; it asks
only while the forge is computing, a bounded number of times, one chain per
card. GitHub is the shared test double of `test_accept_pr`.
"""

import time
from dataclasses import replace

import pytest

from app.domain.review import github_pr, pr_poll
from tests.conftest import wait_work_idle
from tests.integration.test_accept_pr import (
    _arm,
    _poll,
    _protect,
    _ready_card,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.integration.test_pr_poller_notices import _events

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

#: The re-reads' waits, short enough for a test: five of them, as the poller's.
WAITS_S = (0.05,) * 5


@pytest.fixture(autouse=True)
def short_waits(monkeypatch):
    monkeypatch.setattr(pr_poll, "SETTLE_AFTER_S", WAITS_S, raising=False)


def _computing_for(fake, reads: int) -> None:
    """The forge answers "still computing" for the next ``reads`` reads."""
    real = fake.pull_request_status
    left = {"n": reads}

    async def read(**kwargs):
        status = await real(**kwargs)
        if left["n"] > 0:
            left["n"] -= 1
            return replace(status, mergeable=None, mergeable_state="unknown")
        return status

    fake.pull_request_status = read


def _settled() -> None:
    """Wait well past every re-read's wait, short of the next tick."""
    time.sleep(sum(WAITS_S) + 1.5)
    wait_work_idle()


def test_an_armed_card_merges_once_the_forge_has_worked_it_out(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, auto_merge_allowed=True)
    fake.check_state_by_sha[head_sha] = ("pending", "还在跑")
    _poll(client)
    assert _arm(client, cid, "alice").status_code == 200
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _computing_for(fake, 3)

    _poll(client)  # one tick, which finds the forge still computing
    _settled()

    assert [m["sha"] for m in fake.merge_calls] == [head_sha]


def test_repeated_reads_say_a_ready_version_once(client, app_world):
    """The re-reads are reads: the person hears that the version can be
    adopted once, when the forge has answered, and nothing before that."""
    fake = app_world["fake"]
    _pid, tid, _cid, _number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _computing_for(fake, 3)

    _poll(client)
    _settled()

    assert len(_events(client, tid, "accept_ready")) == 1


def test_an_unreachable_forge_is_left_to_the_tick(client, app_world):
    fake = app_world["fake"]
    _pid, _tid, _cid, _number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.status_error = github_pr.GitHubPrError("HTTP 502")
    tried = []
    real = fake.pull_request_status

    async def read(**kwargs):
        tried.append(1)
        return await real(**kwargs)

    fake.pull_request_status = read

    _poll(client)
    _settled()

    assert len(tried) == 1


def test_two_reads_of_one_computing_card_run_one_chain(client, app_world):
    fake = app_world["fake"]
    _pid, _tid, _cid, _number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _computing_for(fake, 100)
    reads = []
    real = fake.pull_request_status

    async def read(**kwargs):
        reads.append(1)
        return await real(**kwargs)

    fake.pull_request_status = read

    _poll(client)
    _poll(client)
    _settled()

    # Two ticks, and one chain of re-reads after them, however long the forge
    # takes; the tick has the card after that.
    assert len(reads) <= 2 + len(WAITS_S)
    assert len(reads) > 2
