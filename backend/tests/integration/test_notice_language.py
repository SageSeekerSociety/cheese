"""A room line and its notification name the sentence they say, so each reader's
screen can say it in that reader's language.

A room is read by several people at once, in different languages, and switching
language re-renders the lines already there. So the stored line is not only the
Chinese text: it carries the catalog key and the parameters it was said with,
and the notification about it carries the same pair. The Chinese text stays
where it was, for the agents that read the room and for browser push.

Same world as `test_pr_poller_notices`: the platform's GitHub App opens the PR
and `review/pr_poll.py` moves the card, as in production.
"""

import pytest

from tests.conftest import seed_user, wait_work_idle
from tests.integration.test_accept_pr import _poll, _ready_card
from tests.integration.test_accept_pr import app_world as _app_world_fixture
from tests.integration.test_pr_poller_notices import _events, _notices

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def test_a_ready_pr_line_names_its_sentence_and_the_notification_carries_it(
    client, app_world
):
    alice = seed_user(client, "alice")
    fake = app_world["fake"]
    _pid, tid, _cid, number, head_sha = _ready_card(client, app_world, number=41)
    fake.check_state_by_sha[head_sha] = ("success", "green")

    _poll(client)
    wait_work_idle()

    (line,) = _events(client, tid, "accept_ready")
    keys = line["meta"]["i18n"]
    assert keys["content"] == {
        "key": "acceptReady",
        "params": {"pr": number, "reviewer": "alice"},
    }
    assert keys["detail_label"] == {"key": "labelNextStep", "params": {}}
    assert keys["detail"]["key"] == "acceptReadyDetail"
    # The Chinese line is still what is stored: agents and push read it.
    assert line["content"] == f"PR #{number} 可以合并了，等 alice 采纳"

    (row,) = _notices(client, alice, "accept_ready")
    assert row["contextMetadata"]["message"] == keys["content"]
    assert row["contextMetadata"]["content"] == line["content"]


def test_a_filed_card_line_names_its_sentence(client, app_world):
    _pid, tid, _cid, _number, _head = _ready_card(client, app_world)

    (line,) = _events(client, tid, "card_filed")
    content = line["meta"]["i18n"]["content"]
    assert content["key"] == "cardFiled"
    assert content["params"] == {"task": "Test delivery", "reviewer": "alice"}
    assert line["content"] == "《Test delivery》已提交，待 alice 审阅"


def test_a_filed_merge_names_the_task_not_the_project(client, app_world):
    """A merge delivers the project's repository, whose name is the project's:
    the channel line has to say which task was handed in."""
    pid, tid, _cid, _number, _head = _ready_card(client, app_world)
    project = client.get(f"/projects/{pid}").json()["data"]

    (line,) = _events(client, tid, "card_filed")

    assert "Test delivery" in line["content"]
    assert f"《{project['name']}》" not in line["content"]
