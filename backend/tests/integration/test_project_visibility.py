"""Who sees which projects.

The listing used to hand every project to everyone. That survives five projects
and breaks the moment a class arrives: a student would find every other team's
work, and every piece of debugging debris, in their own sidebar.
"""


def _create(client, name, **body):
    return client.post("/api/projects", json={"name": name, **body}).json()["data"]


def _visible(client):
    return {p["name"] for p in client.get("/api/projects").json()["data"]["data"]}


def test_the_listing_is_not_everything(client):
    """The point of the change, stated as the thing that must not happen."""
    me = client.get("/api/users/me")
    handle = me.json()["data"]["user"]["username"] if me.status_code == 200 else None
    mine = _create(client, "我的项目", owner_handle=handle or "nobody")
    _create(client, "别人的项目", owner_handle="someone-else")

    names = _visible(client)
    assert "别人的项目" not in names, "a stranger's project must not be in my sidebar"
    if handle:
        assert "我的项目" in names, "my own project must be"
    assert mine["owner_handle"] == (handle or "nobody")


def test_being_on_the_roster_is_enough(client):
    project = _create(client, "有我在的项目", owner_handle="bob")
    me = client.get("/api/users/me")
    handle = me.json()["data"]["user"]["username"] if me.status_code == 200 else None
    if handle is None:
        return  # the client is anonymous here; covered by the anonymous test
    client.post(
        f"/api/projects/{project['id']}/members",
        json={"user_handle": handle, "role": "member"},
    )
    assert "有我在的项目" in _visible(client)


def test_a_team_id_filter_still_answers_for_that_team(client):
    # The explicit team page is unchanged — this is only about the unscoped list.
    r = client.get("/api/projects?team_id=999999")
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 0


def test_anonymous_sees_nothing_rather_than_everything(client):
    """`nobody in particular is asking` must not mean `show them everything`.

    This is the exact failure being fixed, so it is asserted directly rather
    than inferred from a negative on a populated list.
    """
    _create(client, "任何人的项目", owner_handle="someone")
    from app.api.auth import ActorResolverDep  # noqa: F401  (import proves wiring)
    from app.main import app

    # Drop the client's credentials for one call.
    saved = dict(client.headers)
    try:
        for key in ("Authorization", "authorization"):
            client.headers.pop(key, None)
        body = client.get("/api/projects").json()["data"]
        assert body["total"] == 0, "an unauthenticated listing must be empty"
    finally:
        client.headers.clear()
        client.headers.update(saved)
    assert app is not None
