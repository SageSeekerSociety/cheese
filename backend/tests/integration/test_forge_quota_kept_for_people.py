"""Background polling leaves part of the forge quota for people.

A GitHub App installation has one hourly quota for everything the platform
does with a repository. The pollers stop while little of it is left, so a
burst of polling cannot use up what a person delivering a card needs.
"""

import pytest

from tests.integration.test_accept_pr import (
    _cards,
    _FakeTokens,
    _poll,
    _ready_card,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def test_polling_waits_while_the_quota_is_low_and_delivery_does_not(client, app_world):
    fake = app_world["fake"]
    _FakeTokens.quota_left = 900  # of 5000: under the share kept for people

    # A person files a card: that goes through.
    _pid, tid, _cid, number, _head = _ready_card(client, app_world)
    fake.merge_externally(number)
    fake.status_calls.clear()

    _poll(client)

    assert fake.status_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"

    _FakeTokens.quota_left = 4000
    _poll(client)

    assert fake.status_calls
    assert _cards(client, tid)[0]["status"] == "accepted"
