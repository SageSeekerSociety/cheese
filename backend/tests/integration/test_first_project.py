"""A new account is given one project to start in, and only ever one."""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(api_client, person) -> str:
    resp = api_client.post(
        "/users/auth/login",
        json={"username": person.username, "password": person.password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["accessToken"]


def _my_projects(api_client, token) -> list[dict]:
    resp = api_client.get("/projects", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["data"]


def test_a_new_account_gets_a_project_of_its_own(api_client, user_client):
    person = user_client.create_user()
    token = _token(api_client, person)
    resp = api_client.post(
        "/users/me/first-project", json={"name": "林的项目"}, headers=_headers(token)
    )
    assert resp.status_code == 200, resp.text
    made = resp.json()["data"]["project_id"]
    assert made

    projects = _my_projects(api_client, token)
    assert [(p["id"], p["name"], p["owner_handle"]) for p in projects] == [
        (made, "林的项目", person.username)
    ]


def test_asking_again_makes_no_second_project(api_client, user_client):
    """Another tab, another device, or a retry after a lost answer."""
    person = user_client.create_user()
    token = _token(api_client, person)
    first = api_client.post(
        "/users/me/first-project", json={"name": "a"}, headers=_headers(token)
    )
    again = api_client.post(
        "/users/me/first-project", json={"name": "b"}, headers=_headers(token)
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["project_id"] is None
    assert [p["id"] for p in _my_projects(api_client, token)] == [
        first.json()["data"]["project_id"]
    ]


def test_each_account_gets_its_own(api_client, user_client):
    alice, bob = user_client.create_user(), user_client.create_user()
    for person in (alice, bob):
        resp = api_client.post(
            "/users/me/first-project",
            json={"name": "mine"},
            headers=_headers(_token(api_client, person)),
        )
        assert resp.json()["data"]["project_id"]


def test_signed_out_requests_are_refused(api_client):
    resp = api_client.post("/users/me/first-project", json={"name": "x"})
    assert resp.status_code == 401
