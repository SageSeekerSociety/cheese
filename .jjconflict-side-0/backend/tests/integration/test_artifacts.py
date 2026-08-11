"""Render-by-type: 芝士 points at a renderable artifact (cheese artifact),
which becomes the topic's current preview (spec §9.1)."""


def _topic(client) -> tuple[str, str]:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post("/api/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return p["id"], t["id"]


def test_artifact_sets_current_preview(client):
    _pid, tid = _topic(client)
    # No artifact yet → no preview.
    assert client.get(f"/api/topics/{tid}/preview").json()["data"] is None

    r = client.post(f"/api/topics/{tid}/artifact", json={"path": "report.html"})
    assert r.status_code == 200
    block = r.json()["data"]
    assert block["kind"] == "artifact"
    assert block["mime_type"] == "text/html"
    assert block["content"] == "report.html"

    prev = client.get(f"/api/topics/{tid}/preview").json()["data"]
    assert prev == {"kind": "file", "path": "report.html", "mime": "text/html"}


def test_artifact_type_maps_to_mime(client):
    _pid, tid = _topic(client)
    r = client.post(
        f"/api/topics/{tid}/artifact", json={"path": "chart.svg", "as": "svg"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["mime_type"] == "image/svg+xml"
    assert client.get(f"/api/topics/{tid}/preview").json()["data"]["mime"] == (
        "image/svg+xml"
    )


def test_latest_artifact_wins(client):
    # Re-running cheese artifact repoints the current preview to the newest file.
    _pid, tid = _topic(client)
    client.post(f"/api/topics/{tid}/artifact", json={"path": "old.html"})
    client.post(f"/api/topics/{tid}/artifact", json={"path": "new.html"})
    assert client.get(f"/api/topics/{tid}/preview").json()["data"]["path"] == (
        "new.html"
    )


def test_artifact_is_not_in_conversation_timeline(client):
    # An artifact is a preview pointer, not a chat message.
    _pid, tid = _topic(client)
    client.post(f"/api/topics/{tid}/artifact", json={"path": "report.html"})
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    assert not any(b["kind"] == "artifact" for b in blocks)


def test_artifact_rejects_unsupported_type(client):
    _pid, tid = _topic(client)
    r = client.post(
        f"/api/topics/{tid}/artifact", json={"path": "deck.pptx", "as": "slides"}
    )
    assert r.status_code == 422


def test_artifact_rejects_path_traversal(client):
    _pid, tid = _topic(client)
    for bad in ["../etc/passwd", "/abs/report.html", ".git/config"]:
        r = client.post(f"/api/topics/{tid}/artifact", json={"path": bad})
        assert r.status_code == 422, bad


def test_artifact_requires_path(client):
    _pid, tid = _topic(client)
    assert (
        client.post(f"/api/topics/{tid}/artifact", json={"path": ""}).status_code == 422
    )


def test_app_artifact_and_preview(client, monkeypatch):
    """运行环境预览: `cheese serve` declares a RUNNING app; the preview resolves
    the container's published port live and returns kind=app + url."""
    from app.api.routes import topics as topics_routes
    from app.domain.workspace import service as ws

    def _answers(alive: bool):
        async def _probe(_endpoint, **_kw):
            return alive

        return _probe

    pr = client.post("/api/projects", json={"name": "P"})
    pid = pr.json()["data"]["id"]
    tr = client.post("/api/topics", json={"project_id": pid, "title": "T"})
    tid = tr.json()["data"]["id"]

    r = client.post(
        f"/api/topics/{tid}/artifact", json={"path": "Vue dev server", "as": "app"}
    )
    assert r.status_code == 200

    # App up → a url a BROWSER can actually fetch: the backend's reverse proxy,
    # NOT the container's published host port. That port is bound to the server's
    # own loopback, so handing it out (the old behavior) rendered a white frame
    # for everyone except someone running the whole platform locally.
    monkeypatch.setattr(ws, "app_endpoint", lambda t: "127.0.0.1:55007")
    monkeypatch.setattr(topics_routes.proxy, "probe", _answers(True))
    d = client.get(f"/api/topics/{tid}/preview").json()["data"]
    assert d["url"] == f"/api/topics/{tid}/app/", d
    assert "127.0.0.1" not in (d["url"] or ""), "host loopback leaked to the browser"
    assert d["kind"] == "app" and d["path"] == "Vue dev server"
    assert d["container_up"] is True

    # Container up but the server inside it died → no url, but say so distinctly:
    # a published port with nothing answering is exactly the white-frame case.
    monkeypatch.setattr(topics_routes.proxy, "probe", _answers(False))
    d = client.get(f"/api/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "app" and d["url"] is None and d["container_up"] is True

    # Container down → declared but offline (url null), never a crash.
    monkeypatch.setattr(ws, "app_endpoint", lambda t: None)
    d = client.get(f"/api/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "app" and d["url"] is None and d["container_up"] is False

    # A later file artifact supersedes the app as the current preview.
    wt = ws.topic_worktree(__import__("uuid").UUID(pid), __import__("uuid").UUID(tid))
    (wt / "r.html").write_text("<h1>hi</h1>")
    client.post(f"/api/topics/{tid}/artifact", json={"path": "r.html", "as": "html"})
    d = client.get(f"/api/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "file" and d["path"] == "r.html"
