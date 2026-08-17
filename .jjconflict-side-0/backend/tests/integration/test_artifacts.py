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


def test_app_artifact_and_preview(client, monkeypatch):
    """运行环境预览: `cheese serve` declares a RUNNING app; the preview resolves
    the container's published port live and returns kind=app + url."""
    from app.api.routes import topics as topics_routes
    from app.domain.workspace import service as ws

    def _answers(alive: bool):
        async def _probe(_endpoint, **_kw):
            return alive

        return _probe

    pr = client.post("/projects", json={"name": "P"})
    pid = pr.json()["data"]["id"]
    tr = client.post("/topics", json={"project_id": pid, "title": "T"})
    tid = tr.json()["data"]["id"]

    # Declaring an app is only allowed against a runtime that can serve one and a
    # port that answers — see the two refusal tests below.
    monkeypatch.setattr(topics_routes, "app_preview_reachable", lambda _p: True)
    monkeypatch.setattr(ws, "app_endpoint", lambda t: "127.0.0.1:55007")
    monkeypatch.setattr(topics_routes.proxy, "probe", _answers(True))
    r = client.post(
        f"/topics/{tid}/artifact", json={"path": "Vue dev server", "as": "app"}
    )
    assert r.status_code == 200

    # App up → a url a BROWSER can actually fetch: the backend's reverse proxy,
    # NOT the container's published host port. That port is bound to the server's
    # own loopback, so handing it out (the old behavior) rendered a white frame
    # for everyone except someone running the whole platform locally.
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["url"] == f"/api/topics/{tid}/app/", d  # 浏览器侧
    assert "127.0.0.1" not in (d["url"] or ""), "host loopback leaked to the browser"
    assert d["kind"] == "app" and d["path"] == "Vue dev server"
    assert d["container_up"] is True
    assert d["supported"] is True

    # Container up but the server inside it died → no url, but say so distinctly:
    # a published port with nothing answering is exactly the white-frame case.
    monkeypatch.setattr(topics_routes.proxy, "probe", _answers(False))
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "app" and d["url"] is None and d["container_up"] is True

    # Container down → declared but offline (url null), never a crash.
    monkeypatch.setattr(ws, "app_endpoint", lambda t: None)
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "app" and d["url"] is None and d["container_up"] is False

    # A later file artifact supersedes the app as the current preview.
    wt = ws.topic_worktree(__import__("uuid").UUID(pid), __import__("uuid").UUID(tid))
    (wt / "r.html").write_text("<h1>hi</h1>")
    client.post(f"/topics/{tid}/artifact", json={"path": "r.html", "as": "html"})
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "file" and d["path"] == "r.html"


def test_serve_is_refused_when_the_runtime_has_no_container(client, monkeypatch):
    """A topic running on someone's own machine has no container here to publish
    the app port, so `cheese serve` can only ever produce a dead preview. It is
    refused at the API — not merely reported afterwards — because the sandbox's
    `cheese` binary lags this repo by days and a client-side check would not
    reach any agent already running."""
    from app.api.routes import topics as topics_routes

    _pid, tid = _topic(client)
    monkeypatch.setattr(topics_routes, "app_preview_reachable", lambda _p: False)
    r = client.post(
        f"/topics/{tid}/artifact", json={"path": "Vue dev server", "as": "app"}
    )
    assert r.status_code == 422
    assert "cheese artifact" in r.json()["message"], "must name the way that works"
    # And nothing was recorded — an unreachable app must not become the preview.
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


def test_serve_is_refused_when_nothing_answers_on_the_port(client, monkeypatch):
    """The other half of the lie: the runtime CAN host an app, but 芝士 declared
    one before anything was listening. That used to succeed and print 「预览已就
    绪」, and the panel showed a white frame."""
    from app.api.routes import topics as topics_routes
    from app.domain.workspace import service as ws

    async def _dead(_endpoint, **_kw):
        return False

    _pid, tid = _topic(client)
    monkeypatch.setattr(topics_routes, "app_preview_reachable", lambda _p: True)

    # Port not published at all.
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: None)
    r = client.post(f"/topics/{tid}/artifact", json={"path": "app", "as": "app"})
    assert r.status_code == 422

    # Published, but nothing is listening behind it.
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: "127.0.0.1:55007")
    monkeypatch.setattr(topics_routes.proxy, "probe", _dead)
    r = client.post(f"/topics/{tid}/artifact", json={"path": "app", "as": "app"})
    assert r.status_code == 422
    assert str(ws.APP_PORT) in r.json()["message"], "must say which port to use"
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


def test_preview_says_the_runtime_cannot_host_an_app(client, monkeypatch):
    """An app artifact declared while the topic ran in a container, read back
    after it moved to a machine: `container_up` is False for a box that is alive,
    so `supported` is what tells the panel not to say 「再 @ 它一次即可拉起」."""
    from app.api.routes import topics as topics_routes
    from app.domain.workspace import service as ws

    async def _alive(_endpoint, **_kw):
        return True

    _pid, tid = _topic(client)
    monkeypatch.setattr(topics_routes, "app_preview_reachable", lambda _p: True)
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: "127.0.0.1:55007")
    monkeypatch.setattr(topics_routes.proxy, "probe", _alive)
    client.post(f"/topics/{tid}/artifact", json={"path": "app", "as": "app"})

    monkeypatch.setattr(topics_routes, "app_preview_reachable", lambda _p: False)
    monkeypatch.setattr(ws, "app_endpoint", lambda _t: None)
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "app"
    assert d["url"] is None and d["container_up"] is False
    assert d["supported"] is False
