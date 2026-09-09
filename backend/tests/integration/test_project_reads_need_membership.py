"""项目内容对非成员关门：一整片项目级读接口过去谁都能读。

`GET /topics?project_id=` was the only route in this family behind the 项目成员
door. Everything else answered in full **without any credential** — measured on
dev 2026-09-08, not inferred:

    --- no Authorization header at all ---
      GET /projects/{id}            -> 200  name, owner_handle, team_id
      GET /projects/{id}/members    -> 200  the whole roster, names and avatars
      GET /projects/{id}/decisions  -> 200  159 decision records, in full
      GET /projects/{id}/usage      -> 200  tokens and cost
      GET /projects?team_id={tid}   -> 200  every project of a team you are not on

The last one is what made the rest reachable by a stranger who knows nothing:
each row carries the project's `id`, and the id is the key to the four above.
That is also why the same route WITHOUT `team_id` was already strict — the
parameter, not the route, was the way in.

Every assertion below is a status code a browser would receive, from the three
identities that actually show up: nobody, somebody who is simply not on this
project, and a member.
"""

from contextlib import contextmanager

from tests.integration.test_team_member_enters_team_project import (
    _bearer,
    _team,
    _team_project,
)

from tests.conftest import seed_user

# The reads a non-member must not get. Named by what a person loses if it leaks.
READS = {
    "项目本身": "/projects/{pid}",
    "成员名册": "/projects/{pid}/members",
    "决策记录": "/projects/{pid}/decisions",
    "资源用量": "/projects/{pid}/usage",
}


@contextmanager
def off_the_street(client):
    """A caller with nothing: no bearer, and none of the sandbox trust the test
    client normally carries on every request. ``authorize_project`` lets a valid
    sandbox token through as a development credential, so leaving that header on
    would test the trusted path and call it anonymous."""
    saved = client.headers.pop("X-Cheese-Token", None)
    try:
        yield client
    finally:
        if saved is not None:
            client.headers["X-Cheese-Token"] = saved


def _project(client) -> tuple[int, str]:
    """A team project owned by alice, with bob on the team and mallory outside."""
    team_id = _team(client, owner="alice", members=("bob",))
    pid, _ = _team_project(client, owner="alice", team_id=team_id)
    return team_id, pid


def test_a_caller_with_no_credential_reads_nothing(client):
    _, pid = _project(client)
    with off_the_street(client) as anon:
        for what, path in READS.items():
            assert anon.get(path.format(pid=pid)).status_code == 401, what


def test_someone_who_is_not_on_the_project_reads_nothing(client):
    _, pid = _project(client)
    outsider = _bearer(seed_user(client, "mallory"))
    for what, path in READS.items():
        assert client.get(path.format(pid=pid), headers=outsider).status_code == 403, what


def test_a_member_still_reads_everything(client):
    """The half that must not move: shutting the door on strangers is worthless
    if it also shuts it on the people whose project this is."""
    _, pid = _project(client)
    for handle in ("alice", "bob"):  # the owner, and a teammate not on the roster
        who = _bearer(seed_user(client, handle))
        for what, path in READS.items():
            assert client.get(path.format(pid=pid), headers=who).status_code == 200, (
                f"{handle} / {what}"
            )


def test_a_team_project_list_is_not_a_directory(client):
    """`?team_id=` handed out every project id of a team you are not on, and an
    id is all the four routes above ever asked for."""
    team_id, _ = _project(client)
    with off_the_street(client) as anon:
        assert anon.get("/projects", params={"team_id": team_id}).status_code == 401
    outsider = _bearer(seed_user(client, "mallory"))
    assert (
        client.get("/projects", params={"team_id": team_id}, headers=outsider).status_code
        == 403
    )


def test_the_team_still_sees_its_own_projects(client):
    team_id, pid = _project(client)
    for handle in ("alice", "bob"):
        r = client.get(
            "/projects",
            params={"team_id": team_id},
            headers=_bearer(seed_user(client, handle)),
        )
        assert r.status_code == 200, handle
        assert pid in [p["id"] for p in r.json()["data"]["data"]], handle


def test_your_own_project_list_is_untouched(client):
    """`/projects` without `team_id` means "my own projects" and was already
    strict; guarding the parameter must not change it."""
    team_id, pid = _project(client)
    r = client.get("/projects", headers=_bearer(seed_user(client, "alice")))
    assert r.status_code == 200
    assert pid in [p["id"] for p in r.json()["data"]["data"]]
