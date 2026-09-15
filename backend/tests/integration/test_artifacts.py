"""Render-by-type: 芝士 points at a renderable artifact (cheese artifact),
which becomes the topic's current preview (spec §9.1)."""

import uuid

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def content_domain(monkeypatch):
    monkeypatch.setattr(settings, "sites_domain", "content.example.com")
    monkeypatch.setattr(settings, "sites_scheme", "https")


def _topic(client, owner: str | None = None) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return p["id"], t["id"]


def test_a_remote_artifact_is_readable_without_a_git_push(client):
    from tests.integration.conftest import session_auth_headers

    pid, tid = _topic(client, "alice")
    html = "<h1>Result from the remote machine</h1>"
    response = client.post(
        f"/topics/{tid}/artifact",
        json={
            "path": "site/report.html",
            "content": html,
        },
    )
    assert response.status_code == 200, response.text
    response = client.get(
        f"/topics/{tid}/preview/file",
        headers=session_auth_headers("alice"),
        params={
            "topic": tid,
            "path": "site/report.html",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"] == html


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


@pytest.mark.parametrize("size", [100, 1024 * 1024 + 1])
def test_editing_the_same_artifact_changes_preview_version(client, size):
    from app.domain.workspace import service as ws

    pid, tid = _topic(client)
    project, topic = uuid.UUID(pid), uuid.UUID(tid)
    ws.write_room_file(project, topic, "report.html", b"a" * size)
    response = client.post(f"/topics/{tid}/artifact", json={"path": "report.html"})
    assert response.status_code == 200
    first = client.get(f"/topics/{tid}/preview").json()["data"]
    assert first["version"]
    ws.write_room_file(project, topic, "report.html", b"a" * size)
    unchanged = client.get(f"/topics/{tid}/preview").json()["data"]
    assert unchanged["version"] == first["version"]
    ws.write_room_file(project, topic, "report.html", b"b" * size)
    edited = client.get(f"/topics/{tid}/preview").json()["data"]
    assert edited["artifact_id"] == first["artifact_id"]
    assert edited["version"] != first["version"]


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
    """A type the platform has no renderer for is refused here, and the refusal
    says which ones exist — otherwise the caller has to guess twice."""
    _pid, tid = _topic(client)

    r = client.post(f"/topics/{tid}/artifact", json={"path": "scan.tiff", "as": "tiff"})

    assert r.status_code == 422
    assert "html" in r.json()["message"], "must name the types that do work"
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


def test_a_declared_type_is_not_needed_when_the_name_says_it(client):
    """A caller that names `report.docx` has already said what it is.

    Requiring the type to be restated is a step that can be skipped, and
    skipping it used to be silent: the default was html, so a Word file was
    stored as a web page and reached the panel as a mis-typed blob. Reading the
    extension makes the same call land on the right renderer.
    """
    _pid, tid = _topic(client)

    for name, expected in (
        ("评审简报.docx", "wordprocessingml"),
        ("预算.xlsx", "spreadsheetml"),
        ("结题报告.pdf", "application/pdf"),
        ("页面.html", "text/html"),
    ):
        assert (
            client.post(f"/topics/{tid}/artifact", json={"path": name}).status_code
            == 200
        )
        assert expected in client.get(f"/topics/{tid}/preview").json()["data"]["mime"]


def test_an_office_file_survives_the_trip_to_the_platform(client):
    """A .docx is a zip, so it travels base64-encoded and must arrive byte-exact.

    The machine that wrote it is usually not the one serving the panel, so the
    bytes cross the wire; a round trip that mangles them produces a file that
    downloads and then refuses to open.
    """
    import base64

    _pid, tid = _topic(client)
    raw = b"PK\x03\x04binary\x00\xff payload"

    r = client.post(
        f"/topics/{tid}/artifact",
        json={"path": "报告.docx", "content_b64": base64.b64encode(raw).decode()},
    )

    assert r.status_code == 200
    back = client.get(f"/topics/{tid}/attachments/raw?path=报告.docx&download=true")
    assert back.status_code == 200
    assert back.content == raw


def test_malformed_base64_is_refused_rather_than_written(client):
    _pid, tid = _topic(client)

    r = client.post(
        f"/topics/{tid}/artifact",
        json={"path": "报告.docx", "content_b64": "not base64 at all!!"},
    )

    assert r.status_code == 422
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
        assert (
            d["url"] == f"https://preview-{tid.replace('-', '')}.content.example.com/"
        ), d
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

    wt = ws.room_files_root(_uuid.UUID(pid), _uuid.UUID(tid))
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
    assert "cheese_artifact" in r.json()["message"], "must name the way that works"
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


def test_a_word_report_is_converted_so_a_browser_can_show_it(client, monkeypatch):
    """The panel asks for a PDF; the platform converts the .docx into one.

    Without this the 预览 tab has nothing to draw for the format a room most often
    produces, and falls back to a download — the state this route exists to end.
    """
    import base64

    from app.api.routes import topics as topics_routes

    _pid, tid = _topic(client)
    raw = b"PK\x03\x04a word file"
    client.post(
        f"/topics/{tid}/artifact",
        json={"path": "评审简报.docx", "content_b64": base64.b64encode(raw).decode()},
    )

    seen: dict = {}

    async def fake_render(data, path, endpoint, timeout=90.0):
        seen["data"], seen["path"], seen["endpoint"] = data, path, endpoint
        return b"%PDF-1.7 converted"

    monkeypatch.setattr(settings, "office_render_endpoint", "http://renderer:8901")
    monkeypatch.setattr(topics_routes, "render_to_pdf", fake_render)

    r = client.get(f"/topics/{tid}/attachments/pdf", params={"path": "评审简报.docx"})

    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.7 converted"
    assert r.headers["content-type"] == "application/pdf"
    # The file's own bytes go to the renderer, not a path it cannot reach.
    assert seen["data"] == raw


def test_a_spreadsheet_is_never_sent_for_conversion(client):
    """Paginating a sheet breaks its columns apart and throws away the cell
    addresses — the only thing a reader can point at afterwards. The browser
    draws those from the original bytes instead."""
    import base64

    _pid, tid = _topic(client)
    client.post(
        f"/topics/{tid}/artifact",
        json={
            "path": "预算表.xlsx",
            "content_b64": base64.b64encode(b"PK\x03\x04").decode(),
        },
    )

    r = client.get(f"/topics/{tid}/attachments/pdf", params={"path": "预算表.xlsx"})

    assert r.status_code == 422, r.text


def test_a_deployment_without_a_renderer_says_so_rather_than_failing(client):
    """503, not 500: the panel pairs this with the download and a sentence about
    the deployment. A 500 would read as "this file is broken", which it is not."""
    import base64

    _pid, tid = _topic(client)
    client.post(
        f"/topics/{tid}/artifact",
        json={
            "path": "报告.docx",
            "content_b64": base64.b64encode(b"PK\x03\x04").decode(),
        },
    )

    r = client.get(f"/topics/{tid}/attachments/pdf", params={"path": "报告.docx"})

    assert r.status_code == 503, r.text
    assert "文档预览" in r.json()["message"]
