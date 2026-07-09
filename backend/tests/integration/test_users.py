"""User domain CRUD over HTTP."""


def _create(client, **overrides) -> dict:
    body = {"handle": "user-1", "name": "Alice"}
    body.update(overrides)
    return client.post("/api/users", json=body)


def test_create_user(client):
    r = _create(
        client,
        email="alice@example.com",
        bio="hi",
        interests=["ai"],
        skills=["python"],
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["handle"] == "user-1"
    assert data["name"] == "Alice"
    assert data["email"] == "alice@example.com"
    assert data["bio"] == "hi"
    assert data["interests"] == ["ai"]
    assert data["skills"] == ["python"]
    assert "id" in data


def test_create_user_defaults(client):
    r = _create(client)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["email"] is None
    assert data["bio"] == ""
    assert data["interests"] == []
    assert data["skills"] == []


def test_duplicate_handle_rejected(client):
    assert _create(client).status_code == 200
    r = _create(client, name="Other")
    assert r.status_code == 422


def test_get_user_by_handle(client):
    _create(client, name="Alice")
    r = client.get("/api/users/user-1")
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Alice"


def test_get_missing_user_404(client):
    r = client.get("/api/users/nope")
    assert r.status_code == 404


def test_list_users_newest_first(client):
    _create(client, handle="user-1", name="A")
    _create(client, handle="user-2", name="B")
    r = client.get("/api/users")
    body = r.json()
    assert body["code"] == 200
    # 芝士 is a real, pre-seeded agent-user (P1 agent-as-user), so it's the oldest
    # row — ignore it when asserting the two humans' newest-first order.
    handles = [u["handle"] for u in body["data"]["data"] if u["handle"] != "cheese"]
    assert handles == ["user-2", "user-1"]
    assert body["data"]["total"] == 3  # user-2 + user-1 + cheese


def test_update_profile(client):
    _create(client, name="Alice", bio="old", interests=["x"])
    r = client.put(
        "/api/users/user-1",
        json={"name": "Alice2", "interests": ["ai", "ml"]},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["name"] == "Alice2"
    assert data["interests"] == ["ai", "ml"]
    # Unprovided field unchanged.
    assert data["bio"] == "old"


def test_update_missing_user_404(client):
    r = client.put("/api/users/nope", json={"name": "x"})
    assert r.status_code == 404


# ---- 极简登录 (Phase 0): POST /api/users/login, get-or-create by handle ----


def test_login_creates_then_signs_in(client):
    # New handle → registered on the spot; name defaults sensibly.
    r = client.post("/api/users/login", json={"handle": "andyl", "name": "Andy"})
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Andy"
    # Same handle again → same user back (get, not duplicate; name unchanged).
    r = client.post("/api/users/login", json={"handle": "andyl", "name": "Other"})
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Andy"
    # 1 human (andyl) + the pre-seeded 芝士 agent-user (P1 agent-as-user).
    assert client.get("/api/users").json()["data"]["total"] == 2
    # Name omitted → handle doubles as the display name.
    r = client.post("/api/users/login", json={"handle": "bob"})
    assert r.json()["data"]["name"] == "bob"


def test_login_rejects_reserved_and_bad_handles(client):
    # 芝士's identity can never be claimed by a human.
    r = client.post("/api/users/login", json={"handle": "cheese"})
    assert r.status_code == 422
    for bad in ["A B", "Upper", "-lead", "x"]:
        r = client.post("/api/users/login", json={"handle": bad})
        assert r.status_code == 422, bad
