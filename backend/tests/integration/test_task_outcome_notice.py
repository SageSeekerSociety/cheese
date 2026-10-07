"""一个任务的卡采纳了，「合了」那一行落在任务自己的对话里。

任务页读的是任务那段对话：递卡、「可以合并了，等谁采纳」都在那里。采纳的结局要是
落进房间，任务对话的最后一行就永远停在「等 andy 采纳 · 需要手动处理」上。
"""

import time

from tests.delivery import delivery_task_id
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept import _make_card, _make_project, _make_topic
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_pr import _rendered_head
from tests.integration.test_accept_pr import app_world as app_world


def _event_types(client, conversation: str) -> list[str]:
    rows = client.get(
        f"/topics/{conversation}/blocks", headers=session_auth_headers("alice")
    )
    assert rows.status_code == 200, rows.text
    data = rows.json()["data"]
    blocks = data["data"] if isinstance(data, dict) else data
    return [(b.get("meta") or {}).get("event_type") for b in blocks]


def test_the_accept_outcome_lands_in_the_task_conversation(client):
    room = _make_topic(client, _make_project(client))
    task = str(delivery_task_id(client, room))
    card = _make_card(client, room)

    accepted = client.post(
        f"/accept-cards/{card}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card)},
        headers=session_auth_headers("alice"),
    )
    assert accepted.status_code == 200, accepted.text

    # The outcome is told on its own session after the response (it already
    # happened on the forge), so give it a moment to land.
    for _ in range(50):
        if "accept_done" in _event_types(client, task):
            break
        time.sleep(0.1)
    assert "accept_done" in _event_types(client, task)
    assert "accept_done" not in _event_types(client, room)
