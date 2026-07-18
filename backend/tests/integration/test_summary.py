"""一页纸总结 (eval F2) — with the stub agent."""


def test_project_summary_generated_and_stored(client):
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    pid = p["id"]
    assert p["summary"] == ""  # none yet

    r = client.post(f"/api/projects/{pid}/summary")
    assert r.status_code == 200
    assert r.json()["data"]["summary"] == "Hello world"  # stub agent reply

    # Persisted on the project and surfaced on the overview/board.
    got = client.get(f"/api/projects/{pid}").json()["data"]
    assert got["summary"] == "Hello world"


def test_summary_404_for_missing_project(client):
    r = client.post("/api/projects/00000000-0000-0000-0000-000000000000/summary")
    assert r.status_code == 404
