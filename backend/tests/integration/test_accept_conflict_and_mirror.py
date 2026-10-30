"""Two rules about a card's merge state, stated before the code:

- accepting a PR that conflicts with its base stops the accept and tells the
  task's agent to resolve it, the same notice the poller sends;
- a reading of the card's merge state never puts back a clock that a newer
  reading already ended: `since` says when the state shown started holding,
  and a reading that began before another one was written must not restore
  the older state's start.
"""

import asyncio
import threading
import uuid

import pytest

from app.domain.review.repositories import AcceptCardRepository
from tests.conftest import wait_work_idle
from tests.integration.test_a_newer_pr_notice_replaces_the_unsent_one import _owed
from tests.integration.test_accept_pr import (
    _accept,
    _cards,
    _poll,
    _ready_card,
    _set_merge_since,
)
from tests.integration.test_accept_pr import app_world as _app_world_fixture

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

_LONG_AGO = "2020-01-01T00:00:00+00:00"


def test_accepting_a_conflicting_pr_tells_the_agent_to_resolve_it(client, app_world):
    fake = app_world["fake"]
    _, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.mergeable_by_number[number] = False

    r = _accept(client, cid)
    wait_work_idle()

    assert r.status_code == 422, r.text
    assert [kind for kind, _state, _ in _owed(client, cid)] == ["pr_conflict"]
    assert fake.merge_calls == []


def _another_reading_writes(client, card_id: str, state: str, since: str) -> None:
    async def write() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(uuid.UUID(card_id))
            card.merge_state = {**card.merge_state, "state": state, "since": since}
            await s.commit()

    asyncio.run(write())


def test_a_reading_in_flight_does_not_restore_a_clock_a_newer_reading_ended(
    client, app_world
):
    fake = app_world["fake"]
    _, tid, cid, _number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Lint: failure")
    _poll(client)
    wait_work_idle()
    held = _cards(client, tid)[0]["merge_state"]["state"]
    _set_merge_since(client, cid, _LONG_AGO)

    entered, release = threading.Event(), threading.Event()
    fake.status_gate = (entered, release)
    poll = threading.Thread(target=_poll, args=(client,))
    poll.start()
    assert entered.wait(30)
    fake.status_gate = None
    # A newer reading saw the PR in another state; the one in flight began
    # before it and still sees the old one.
    _another_reading_writes(client, cid, "clean", "2026-01-01T00:00:00+00:00")
    release.set()
    poll.join(60)
    wait_work_idle()

    mirror = _cards(client, tid)[0]["merge_state"]
    assert mirror["state"] == held
    assert mirror["since"] != _LONG_AGO
