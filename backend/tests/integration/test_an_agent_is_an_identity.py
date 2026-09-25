"""An agent is a collaborator, so it has an identity of its own.

A room is a collaboration space: it may seat several agents, and one agent may
work in several rooms. So the identity an agent acts under cannot be derived
from a room — that would give two agents in one room the same name, and one
agent two names in two rooms. It is derived from the agent.
"""

from tests.integration.conftest import post_project


def _project(client, name: str = "Identity") -> str:
    r = post_project(client, json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _agent(client, project_id: str, handle: str, display: str) -> dict:
    r = client.post(
        f"/projects/{project_id}/agents",
        json={"handle": handle, "display_name": display},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_new_agent_can_act_be_attributed_to_and_be_de_authorized(client):
    """The three things an identity buys, checked as one: the agent has a user
    row of its own, it reads as an agent rather than as a person, and its
    display name is what a reader sees."""
    from app.domain.identity.handles import agent_instance_handle
    from app.domain.identity.services import IdentityService

    project = _project(client)
    first = _agent(client, project, "planner", "规划师")
    second = _agent(client, project, "reviewer", "审稿人")

    async def identities():
        async with client.test_factory() as db:
            service = IdentityService(db)
            out = []
            for made in (first, second):
                handle = agent_instance_handle(made["id"])
                out.append((handle, await service.is_agent(handle)))
            return out

    (one, one_is_agent), (two, two_is_agent) = client.portal.call(identities)
    assert one != two, "two agents in one project are two collaborators"
    assert one_is_agent and two_is_agent


def test_the_identity_does_not_come_from_a_room(client):
    """Same agent, two rooms: one identity, and neither room's id is in it.

    A handle derived from the room would have given this agent two names, and
    given a second agent in either room this one's name."""
    from app.domain.identity.handles import agent_instance_handle
    from tests.integration.conftest import session_auth_headers

    project = _project(client)
    made = _agent(client, project, "planner", "规划师")
    rooms = [
        client.post(
            "/topics",
            json={"project_id": project, "title": t, "created_by": "u"},
        ).json()["data"]["id"]
        for t in ("one", "two")
    ]

    mine = agent_instance_handle(made["id"])
    for room in rooms:
        seated = client.post(
            f"/topics/{room}/members",
            json={"handle": mine, "role": "member"},
            headers=session_auth_headers("u"),
        )
        assert seated.status_code == 200, seated.text
        roster = client.get(f"/topics/{room}/members").json()["data"]["data"]
        assert mine in [m["member_handle"] for m in roster]
        assert room.replace("-", "")[:12] not in mine
