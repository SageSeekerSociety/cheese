"""Two teammates in one room are two names on the roster.

The member panel reads an agent row's name from here. One name for every agent
seat showed two teammates as the same person — and with the mention roster
having had the same defect, a reader could neither tell them apart nor address
the second one.
"""

from app.domain.identity.handles import agent_instance_handle
from tests.integration.conftest import session_auth_headers


def test_two_seated_agents_show_their_own_names(client):
    project = client.post("/projects", json={"name": "Two names"}).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room", "created_by": "alice"},
    ).json()["data"]

    made = []
    for handle, display in (("planner", "规划师"), ("reviewer", "审稿人")):
        r = client.post(
            f"/projects/{project['id']}/agents",
            json={"handle": handle, "display_name": display},
        )
        assert r.status_code == 200, r.text
        made.append(r.json()["data"])

    for agent in made:
        seat = agent_instance_handle(agent["id"])
        r = client.post(
            f"/topics/{topic['id']}/members",
            json={"handle": seat, "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text

    roster = client.get(f"/topics/{topic['id']}/members").json()["data"]["data"]
    names = {row["name"] for row in roster if row["agent"]}
    assert {"规划师", "审稿人"} <= names, names
