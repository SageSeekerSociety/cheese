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

    r = client.post(f"/topics/{tid}/artifact", json={"path": "slides.pdf", "as": "pdf"})

    assert r.status_code == 422
    assert "html" in r.json()["message"], "must name the types that do work"
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


# --- 运行环境预览: an artifact that is a RUNNING app, not a file ----------------


def _preview_machine(topic_id: str, *, alive: bool):
    """A machine whose preview helper is connected, with or without an app behind
    it. Attaches to the real hub over the real frame codec — the two states the
    panel has to tell apart are exactly what a probe through the tunnel decides.
    """
    import uuid

    from app.domain.agent import preview_tunnel as wire
    from app.domain.agent.preview_hub import preview_hub

    tid = uuid.UUID(topic_id)

    class _Machine:
        async def send_bytes(self, data: bytes) -> None:
            op, stream, _payload = wire.decode(data)
            if op != wire.OP_REQ:
                return
            if not alive:
                preview_hub.on_frame(
                    tid, wire.encode(wire.OP_ERR, stream, b"connection refused")
                )
                return
            preview_hub.on_frame(
                tid,
                wire.encode(
                    wire.OP_RESP,
                    stream,
                    wire.encode_meta({"status": 200, "headers": []}, b"ok"),
                ),
            )
            preview_hub.on_frame(tid, wire.encode(wire.OP_END, stream))

    machine = _Machine()
    preview_hub.attach(tid, machine)
    return machine


def _detach(topic_id: str, machine) -> None:
    import uuid

    from app.domain.agent.preview_hub import preview_hub

    preview_hub.detach(uuid.UUID(topic_id), machine)


def test_app_artifact_and_preview(client):
    """`cheese serve` declares a RUNNING app; the preview knocks on it live and
    hands the browser a path a browser can actually fetch."""
    from app.domain.workspace import service as ws

    pr = client.post("/projects", json={"name": "P"})
    pid = pr.json()["data"]["id"]
    tr = client.post("/topics", json={"project_id": pid, "title": "T"})
    tid = tr.json()["data"]["id"]

    machine = _preview_machine(tid, alive=True)
    try:
        r = client.post(
            f"/topics/{tid}/artifact", json={"path": "Vue dev server", "as": "app"}
        )
        assert r.status_code == 200, r.text

        d = client.get(f"/topics/{tid}/preview").json()["data"]
        assert d["kind"] == "app" and d["path"] == "Vue dev server"
        # The backend's reverse proxy — never an address on the machine, which is
        # somebody's laptop behind NAT and means nothing to a browser here.
        assert d["url"] == f"/api/topics/{tid}/app/", d
        assert "127.0.0.1" not in (d["url"] or ""), "a machine address leaked out"
        assert d["tunnel_up"] is True
    finally:
        _detach(tid, machine)

    # Tunnel up, app dead → no url, and said distinctly: a live tunnel with
    # nothing behind it is exactly the white-frame case.
    dead = _preview_machine(tid, alive=False)
    try:
        d = client.get(f"/topics/{tid}/preview").json()["data"]
        assert d["kind"] == "app" and d["url"] is None and d["tunnel_up"] is True
    finally:
        _detach(tid, dead)

    # Machine gone entirely → declared but offline, never a crash.
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "app" and d["url"] is None and d["tunnel_up"] is False

    # A later file artifact supersedes the app as the current preview.
    import uuid as _uuid

    wt = ws.topic_worktree(_uuid.UUID(pid), _uuid.UUID(tid))
    (wt / "r.html").write_text("<h1>hi</h1>")
    client.post(f"/topics/{tid}/artifact", json={"path": "r.html", "as": "html"})
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "file" and d["path"] == "r.html"


def test_serve_is_refused_when_the_machine_carries_no_preview_out(client, monkeypatch):
    """No tunnel means the running app cannot reach the panel at all. Refused at
    the API rather than merely reported afterwards, so 芝士 never announces
    「预览已就绪」 over a white frame."""
    from app.api.routes import topics as topics_routes

    _pid, tid = _topic(client)
    # Don't sit through the real grace window for a machine that is not coming.
    monkeypatch.setattr(topics_routes, "_PREVIEW_ATTACH_WAIT_S", 0.05)

    r = client.post(
        f"/topics/{tid}/artifact", json={"path": "Vue dev server", "as": "app"}
    )

    assert r.status_code == 422, r.text
    assert "cheese artifact" in r.json()["message"], "must name the way that works"
    # And nothing was recorded — an unreachable app must not become the preview.
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


def test_serve_is_refused_when_nothing_answers_on_the_declared_port(client):
    """The other half of the lie: the tunnel is up, but 芝士 declared a preview
    before anything was listening. That used to succeed, and the panel showed a
    white frame."""
    _pid, tid = _topic(client)

    dead = _preview_machine(tid, alive=False)
    try:
        r = client.post(f"/topics/{tid}/artifact", json={"path": "app", "as": "app"})
    finally:
        _detach(tid, dead)

    assert r.status_code == 422, r.text
    assert "端口" in r.json()["message"], "must say what is wrong with the port"
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None
