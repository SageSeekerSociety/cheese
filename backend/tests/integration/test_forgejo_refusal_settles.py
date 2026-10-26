"""Forgejo's "cannot merge" becomes a conflict only once it has lasted.

Forgejo answers `mergeable: false` while its merge check of a PR is still
running, which it does again after every push to either branch, and also when
the PR conflicts. Nothing in one answer tells the two apart; time does: the
check finishes within seconds. The rules, as stated before the code:

- a PR Forgejo refuses is "not yet known" until the refusal of the same head
  and base has lasted two minutes: no conflict on the card, no conflict notice
  to the agent;
- past that, it is a conflict: the card turns dirty and the agent is told;
- a push starts a new merge check, so the two minutes start again.
"""

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from tests.conftest import wait_work_idle
from tests.integration.test_accept_pr import (
    _accept,
    _cards,
    _poll,
    _ready_card,
    _room_event_settled,
)
from tests.integration.test_accept_pr import app_world as _app_world_fixture
from tests.integration.test_nudge_pr_signals import _authorized, _nudges

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _refused_by_forgejo(fake, number: int) -> None:
    """The PR as the Forgejo client reads `mergeable: false` on it."""
    fake.mergeable_by_number[number] = None
    fake.mergeable_state_by_number[number] = "refused"


def _refusal_began_minutes_ago(client, card_id: str, minutes: int) -> None:
    """Move back the moment the card first saw Forgejo refuse this pair."""
    from app.domain.review.repositories import AcceptCardRepository

    async def rewind() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(uuid.UUID(card_id))
            mirror = dict(card.merge_state or {})
            record = mirror.get("forge_refused")
            if isinstance(record, dict):
                since = datetime.fromisoformat(record["since"])
                mirror["forge_refused"] = {
                    **record,
                    "since": (since - timedelta(minutes=minutes)).isoformat(),
                }
                card.merge_state = mirror
                await s.commit()

    asyncio.run(rewind())


def test_a_refusal_while_forgejo_is_still_checking_is_not_a_conflict(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, _head = _authorized(client, app_world)
    _refused_by_forgejo(fake, number)

    _poll(client)
    wait_work_idle()

    assert _cards(client, tid)[0]["merge_state"]["state"] == "unknown"
    assert _nudges(client, tid, "pr_conflict") == []


def test_a_refusal_that_lasts_with_the_same_head_and_base_is_a_conflict(
    client, app_world
):
    fake = app_world["fake"]
    tid, cid, number, _head = _authorized(client, app_world)
    _refused_by_forgejo(fake, number)
    _poll(client)
    wait_work_idle()

    _refusal_began_minutes_ago(client, cid, 3)
    _poll(client)
    wait_work_idle()

    assert _cards(client, tid)[0]["merge_state"]["state"] == "dirty"
    assert len(_nudges(client, tid, "pr_conflict")) == 1


def test_a_push_starts_the_wait_again(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, _head = _authorized(client, app_world)
    _refused_by_forgejo(fake, number)
    _poll(client)
    wait_work_idle()
    _refusal_began_minutes_ago(client, cid, 3)

    fake.push_new_commit(number)
    _poll(client)
    wait_work_idle()

    assert _cards(client, tid)[0]["merge_state"]["state"] == "unknown"
    assert _nudges(client, tid, "pr_conflict") == []


def test_accepting_while_forgejo_is_still_checking_does_not_say_conflict(
    client, app_world
):
    fake = app_world["fake"]
    _, _tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _refused_by_forgejo(fake, number)

    r = _accept(client, cid)

    assert r.status_code == 422, r.text
    assert "冲突" not in r.json()["message"]
    assert fake.merge_calls == []


def test_accepting_after_a_lasting_refusal_stops_and_hands_it_to_the_agent(
    client, app_world
):
    fake = app_world["fake"]
    _, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _refused_by_forgejo(fake, number)
    _poll(client)
    wait_work_idle()
    _refusal_began_minutes_ago(client, cid, 3)

    r = _accept(client, cid)

    assert r.status_code == 422, r.text
    assert "冲突" in r.json()["message"]
    assert fake.merge_calls == []
    assert _room_event_settled(client, tid, "accept_conflict") == 1
