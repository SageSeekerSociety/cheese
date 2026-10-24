"""`POST /topics/{id}/messages` hands the message's writer what the request
already read of the project: always its row, and its roster where letting the
person speak read it (综合's main line). `prepare_human` is spied on, not
replaced: the message is still written."""

import pytest

from app.domain.agent.turn.intake import human
from tests.integration.conftest import post_message, post_project, session_auth_headers


@pytest.fixture
def handoffs(monkeypatch) -> list:
    seen: list = []
    prepare = human.prepare_human

    async def spy(session, place, content, author, *, handoff=None):
        seen.append(handoff)
        return await prepare(session, place, content, author, handoff=handoff)

    monkeypatch.setattr(human, "prepare_human", spy)
    return seen


def test_the_general_channel_hands_over_the_project_and_its_roster(client, handoffs):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]

    sent = post_message(
        client, project["root_topic_id"], "alice", {"content": "@alice 看一下"}
    )

    assert "<@alice>" in sent["content"]
    [handoff] = handoffs
    assert handoff.project is not None and str(handoff.project.id) == project["id"]
    assert (
        handoff.place is not None
        and str(handoff.place.room_id) == project["root_topic_id"]
    )
    assert handoff.roster is not None
    assert "alice" in {member.handle for member in handoff.roster}


def test_another_channel_hands_over_the_project_alone(client, handoffs):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]

    sent = post_message(client, topic["id"], "alice", {"content": "@alice 看一下"})

    # Speaking here asks the channel's members, not the project's roster: there
    # is no roster to hand over, and the writer reads it itself.
    assert "<@alice>" in sent["content"]
    [handoff] = handoffs
    assert handoff.project is not None and str(handoff.project.id) == project["id"]
    assert handoff.place is not None and str(handoff.place.room_id) == topic["id"]
    assert handoff.roster is None
