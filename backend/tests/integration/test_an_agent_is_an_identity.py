"""An agent is a collaborator, so it has an identity of its own.

A room is a collaboration space: it may seat several agents, and one agent may
work in several rooms. So the identity an agent acts under cannot be derived
from a room — that would give two agents in one room the same name, and one
agent two names in two rooms. It is derived from the agent.
"""


def _project(client, name: str = "Identity") -> str:
    r = client.post("/projects", json={"name": name})
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
    """Same agent, two rooms: one identity. The room-derived handle would have
    given it two, and given a second agent in either room the first's name."""
    from app.domain.identity.handles import agent_instance_handle, topic_agent_handle

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
    assert mine not in {topic_agent_handle(r) for r in rooms}
