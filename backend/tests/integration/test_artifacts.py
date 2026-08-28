"""Render-by-type: 芝士 points at a renderable artifact (cheese artifact),
which becomes the topic's current preview (spec §9.1)."""


def _topic(client) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return p["id"], t["id"]


def test_artifact_sets_current_preview(client):
    _pid, tid = _topic(client)
    # No artifact yet → no preview.
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None

    r = client.post(f"/topics/{tid}/artifact", json={"path": "report.html"})
    assert r.status_code == 200
    block = r.json()["data"]
    assert block["kind"] == "artifact"
    assert block["mime_type"] == "text/html"
    assert block["content"] == "report.html"

    prev = client.get(f"/topics/{tid}/preview").json()["data"]
    assert prev["kind"] == "file"
    assert prev["path"] == "report.html"
    assert prev["mime"] == "text/html"
    # Identifies WHICH artifact this is, so a client can tell a new preview from
    # a re-fetch of the same one (re-pointing at the same path is a new preview).
    assert prev["artifact_id"] == block["id"]


def test_artifact_type_maps_to_mime(client):
    _pid, tid = _topic(client)
    r = client.post(f"/topics/{tid}/artifact", json={"path": "chart.svg", "as": "svg"})
    assert r.status_code == 200
    assert r.json()["data"]["mime_type"] == "image/svg+xml"
    assert client.get(f"/topics/{tid}/preview").json()["data"]["mime"] == (
        "image/svg+xml"
    )


def test_latest_artifact_wins(client):
    # Re-running cheese artifact repoints the current preview to the newest file.
    _pid, tid = _topic(client)
    client.post(f"/topics/{tid}/artifact", json={"path": "old.html"})
    client.post(f"/topics/{tid}/artifact", json={"path": "new.html"})
    assert client.get(f"/topics/{tid}/preview").json()["data"]["path"] == ("new.html")


def test_artifact_is_not_in_conversation_timeline(client):
    # An artifact is a preview pointer, not a chat message.
    _pid, tid = _topic(client)
    client.post(f"/topics/{tid}/artifact", json={"path": "report.html"})
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert not any(b["kind"] == "artifact" for b in blocks)


def test_artifact_rejects_unsupported_type(client):
    _pid, tid = _topic(client)
    r = client.post(
        f"/topics/{tid}/artifact", json={"path": "deck.pptx", "as": "slides"}
    )
    assert r.status_code == 422


def test_artifact_rejects_path_traversal(client):
    _pid, tid = _topic(client)
    for bad in ["../etc/passwd", "/abs/report.html", ".git/config"]:
        r = client.post(f"/topics/{tid}/artifact", json={"path": bad})
        assert r.status_code == 422, bad


def test_artifact_requires_path(client):
    _pid, tid = _topic(client)
    assert client.post(f"/topics/{tid}/artifact", json={"path": ""}).status_code == 422


def test_an_unknown_artifact_type_is_refused_by_name(client):
    """The renderer is chosen from what 芝士 DECLARED, never guessed from an
    extension — so a type the platform has no renderer for has to be refused
    here, and the refusal has to say which ones exist."""
    _pid, tid = _topic(client)

    r = client.post(f"/topics/{tid}/artifact", json={"path": "app.py", "as": "app"})

    assert r.status_code == 422
    assert "html" in r.json()["message"], "must name the types that do work"
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None
