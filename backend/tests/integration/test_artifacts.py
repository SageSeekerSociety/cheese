"""Render-by-type: 芝士 points at a renderable artifact (cheese show),
which becomes the topic's current preview (spec §9.1)."""

import uuid

import pytest

from app.core.config import settings
from tests.integration.conftest import post_project, room_socket


@pytest.fixture(autouse=True)
def content_domain(monkeypatch):
    monkeypatch.setattr(settings, "sites_domain", "content.example.com")
    monkeypatch.setattr(settings, "sites_scheme", "https")


def _topic(client, owner: str | None = None) -> tuple[str, str]:
    p = post_project(client, json={"name": "P"}, owner=owner).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return p["id"], t["id"]


def test_a_remote_artifact_is_readable_without_a_git_push(client):
    from tests.integration.conftest import session_auth_headers

    pid, tid = _topic(client, "alice")
    html = "<h1>Result from the remote machine</h1>"
    response = client.post(
        f"/topics/{tid}/shown",
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


def test_a_room_file_reads_by_path_and_not_only_as_the_current_preview(client):
    """A `<&path>` chip names a file, and the room may have moved on since.

    The chip is a path with no store behind it, and a room's own files are not
    on any branch — so this is the read that gets a reader from that chip to
    the file. Reading only the pinned artifact would answer with whatever 芝士
    delivered last instead.
    """
    from tests.integration.conftest import session_auth_headers

    _pid, tid = _topic(client, "alice")
    headers = session_auth_headers("alice")
    for path, content in (("初稿.md", "# 初稿\n"), ("定稿.md", "# 定稿\n")):
        made = client.post(
            f"/topics/{tid}/shown", json={"path": path, "content": content}
        )
        assert made.status_code == 200, made.text

    earlier = client.get(
        f"/topics/{tid}/preview/file", headers=headers, params={"path": "初稿.md"}
    )
    assert earlier.status_code == 200, earlier.text
    assert earlier.json()["data"]["content"] == "# 初稿\n"

    current = client.get(f"/topics/{tid}/preview/file", headers=headers)
    assert current.json()["data"]["path"] == "定稿.md"


def test_a_room_file_read_stays_inside_the_room(client):
    from tests.integration.conftest import session_auth_headers

    _pid, tid = _topic(client, "alice")
    for bad in ["../../etc/passwd", "/etc/passwd", ".git/config"]:
        answer = client.get(
            f"/topics/{tid}/preview/file",
            headers=session_auth_headers("alice"),
            params={"path": bad},
        )
        assert answer.status_code == 422, bad


def test_artifact_sets_current_preview(client):
    _pid, tid = _topic(client)
    # No artifact yet → no preview.
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None

    r = client.post(f"/topics/{tid}/shown", json={"path": "report.html"})
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
    r = client.post(f"/topics/{tid}/shown", json={"path": "chart.svg", "as": "svg"})
    assert r.status_code == 200
    assert r.json()["data"]["mime_type"] == "image/svg+xml"
    assert client.get(f"/topics/{tid}/preview").json()["data"]["mime"] == (
        "image/svg+xml"
    )


@pytest.mark.parametrize("size", [100, 1024 * 1024 + 1])
def test_editing_the_same_artifact_changes_preview_version(client, size):
    from app.domain.library import service as library

    pid, tid = _topic(client)
    project, topic = uuid.UUID(pid), uuid.UUID(tid)
    library.write_room_file(project, topic, "report.html", b"a" * size)
    response = client.post(f"/topics/{tid}/shown", json={"path": "report.html"})
    assert response.status_code == 200
    first = client.get(f"/topics/{tid}/preview").json()["data"]
    assert first["version"]
    library.write_room_file(project, topic, "report.html", b"a" * size)
    unchanged = client.get(f"/topics/{tid}/preview").json()["data"]
    assert unchanged["version"] == first["version"]
    library.write_room_file(project, topic, "report.html", b"b" * size)
    edited = client.get(f"/topics/{tid}/preview").json()["data"]
    assert edited["artifact_id"] == first["artifact_id"]
    assert edited["version"] != first["version"]


def test_latest_artifact_wins(client):
    # Re-running cheese show repoints the current preview to the newest file.
    _pid, tid = _topic(client)
    client.post(f"/topics/{tid}/shown", json={"path": "old.html"})
    client.post(f"/topics/{tid}/shown", json={"path": "new.html"})
    assert client.get(f"/topics/{tid}/preview").json()["data"]["path"] == ("new.html")


def test_a_shown_file_is_in_the_conversation(client):
    # 芝士 putting something in front of the room is something it said: the chat
    # shows it as a card. Every earlier one stays there too — the preview tab
    # only ever shows the last.
    _pid, tid = _topic(client)
    first = client.post(f"/topics/{tid}/shown", json={"path": "old.html"}).json()
    second = client.post(f"/topics/{tid}/shown", json={"path": "report.html"}).json()
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    shown = [b for b in blocks if b["kind"] == "artifact"]
    assert [b["id"] for b in shown] == [first["data"]["id"], second["data"]["id"]]
    assert [b["content"] for b in shown] == ["old.html", "report.html"]


def test_a_shown_file_reaches_the_room_live(client):
    # The reader is usually in the room while 芝士 works: the card appears then,
    # not on the next reload.
    _pid, tid = _topic(client, owner="alice")
    with room_socket(client, tid, "alice") as ws:
        block = client.post(f"/topics/{tid}/shown", json={"path": "report.html"})
        frame = ws.receive_json()
    assert frame == {"type": "assistant_block", "block": block.json()["data"]}


def test_artifact_rejects_unsupported_type(client):
    _pid, tid = _topic(client)
    r = client.post(f"/topics/{tid}/shown", json={"path": "deck.pptx", "as": "slides"})
    assert r.status_code == 422


def test_artifact_rejects_path_traversal(client):
    _pid, tid = _topic(client)
    for bad in ["../etc/passwd", "/abs/report.html", ".git/config"]:
        r = client.post(f"/topics/{tid}/shown", json={"path": bad})
        assert r.status_code == 422, bad


def test_artifact_requires_path(client):
    _pid, tid = _topic(client)
    assert client.post(f"/topics/{tid}/shown", json={"path": ""}).status_code == 422


def test_an_unknown_artifact_type_is_refused_by_name(client):
    """A type the platform has no renderer for is refused here, and the refusal
    says which ones exist — otherwise the caller has to guess twice."""
    _pid, tid = _topic(client)

    r = client.post(f"/topics/{tid}/shown", json={"path": "scan.tiff", "as": "tiff"})

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
        ("动画.gif", "image/gif"),
        ("图.webp", "image/webp"),
    ):
        assert (
            client.post(f"/topics/{tid}/shown", json={"path": name}).status_code == 200
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
        f"/topics/{tid}/shown",
        json={"path": "报告.docx", "content_b64": base64.b64encode(raw).decode()},
    )

    assert r.status_code == 200
    back = client.get(f"/topics/{tid}/attachments/raw?path=报告.docx&download=true")
    assert back.status_code == 200
    assert back.content == raw


def test_malformed_base64_is_refused_rather_than_written(client):
    _pid, tid = _topic(client)

    r = client.post(
        f"/topics/{tid}/shown",
        json={"path": "报告.docx", "content_b64": "not base64 at all!!"},
    )

    assert r.status_code == 422
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


# --- 运行环境预览: an artifact that is a RUNNING app, not a file ----------------


def _room_agent(client, topic_id: str) -> str:
    """The teammate a room answers as — who a declaration made without a
    teammate's credential is recorded as, and so whose tunnel it follows."""
    body = client.get(f"/topics/{topic_id}/members")
    assert body.status_code == 200, body.text
    return next(
        row["member_handle"] for row in body.json()["data"]["data"] if row["agent"]
    )


def _preview_machine(client, topic_id: str, *, alive: bool):
    """A machine whose preview helper is connected, with or without an app behind
    it. Attaches to the real hub over the real frame codec — the two states the
    panel has to tell apart are exactly what a probe through the tunnel decides.
    """
    import uuid

    from app.domain.agent import preview_tunnel as wire
    from app.domain.agent.preview_hub import PreviewMachine, preview_hub

    tid = uuid.UUID(topic_id)

    class _Machine:
        attached: PreviewMachine | None = None

        def reply(self, frame: bytes) -> None:
            assert self.attached is not None
            self.attached.on_frame(frame)

        async def send_bytes(self, data: bytes) -> None:
            op, stream, _payload = wire.decode(data)
            if op != wire.OP_REQ:
                return
            if not alive:
                self.reply(wire.encode(wire.OP_ERR, stream, b"connection refused"))
                return
            self.reply(
                wire.encode(
                    wire.OP_RESP,
                    stream,
                    wire.encode_meta({"status": 200, "headers": []}, b"ok"),
                ),
            )
            self.reply(wire.encode(wire.OP_END, stream))

    machine = _Machine()
    machine.attached = preview_hub.attach(tid, _room_agent(client, topic_id), machine)
    return machine


def _detach(topic_id: str, machine) -> None:
    from app.domain.agent.preview_hub import preview_hub

    preview_hub.detach(machine.attached)


def test_app_artifact_and_preview(client):
    """`cheese serve` declares a RUNNING app; the preview knocks on it live and
    hands the browser a path a browser can actually fetch."""
    from app.domain.library import service as library

    pr = post_project(client, json={"name": "P"})
    pid = pr.json()["data"]["id"]
    tr = client.post("/topics", json={"project_id": pid, "title": "T"})
    tid = tr.json()["data"]["id"]

    machine = _preview_machine(client, tid, alive=True)
    try:
        r = client.post(
            f"/topics/{tid}/shown", json={"path": "Vue dev server", "as": "app"}
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
    dead = _preview_machine(client, tid, alive=False)
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

    wt = library.room_files_root(_uuid.UUID(pid), _uuid.UUID(tid))
    (wt / "r.html").write_text("<h1>hi</h1>")
    client.post(f"/topics/{tid}/shown", json={"path": "r.html", "as": "html"})
    d = client.get(f"/topics/{tid}/preview").json()["data"]
    assert d["kind"] == "file" and d["path"] == "r.html"


def test_serve_is_refused_when_the_machine_carries_no_preview_out(client, monkeypatch):
    """No tunnel means the running app cannot reach the panel at all. Refused at
    the API rather than merely reported afterwards, so 芝士 never announces
    「预览已就绪」 over a white frame."""
    from app.api.routes import topics_shown

    _pid, tid = _topic(client)
    # Don't sit through the real grace window for a machine that is not coming.
    # The window lives where the handler that reads it does now.
    monkeypatch.setattr(topics_shown, "_PREVIEW_ATTACH_WAIT_S", 0.05)

    r = client.post(
        f"/topics/{tid}/shown", json={"path": "Vue dev server", "as": "app"}
    )

    assert r.status_code == 422, r.text
    assert "cheese show" in r.json()["message"], "must name the way that works"
    # And nothing was recorded — an unreachable app must not become the preview.
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


def test_serve_is_refused_when_nothing_answers_on_the_declared_port(client):
    """The other half of the lie: the tunnel is up, but 芝士 declared a preview
    before anything was listening. That used to succeed, and the panel showed a
    white frame."""
    _pid, tid = _topic(client)

    dead = _preview_machine(client, tid, alive=False)
    try:
        r = client.post(f"/topics/{tid}/shown", json={"path": "app", "as": "app"})
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

    from app.api.routes import topics_attachments

    _pid, tid = _topic(client)
    raw = b"PK\x03\x04a word file"
    client.post(
        f"/topics/{tid}/shown",
        json={"path": "评审简报.docx", "content_b64": base64.b64encode(raw).decode()},
    )

    seen: dict = {}

    async def fake_render(data, path, endpoint, timeout=90.0):
        seen["data"], seen["path"], seen["endpoint"] = data, path, endpoint
        return b"%PDF-1.7 converted"

    monkeypatch.setattr(settings, "office_render_endpoint", "http://renderer:8901")
    monkeypatch.setattr(topics_attachments, "render_to_pdf", fake_render)

    r = client.get(f"/topics/{tid}/attachments/pdf", params={"path": "评审简报.docx"})

    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.7 converted"
    assert r.headers["content-type"] == "application/pdf"
    # The file's own bytes go to the renderer, not a path it cannot reach.
    assert seen["data"] == raw
    from app.domain.textfile import content_version

    assert r.headers["x-cheese-source-version"] == content_version(raw)
    assert r.headers["cache-control"] == "no-store"


def test_pdf_source_version_binds_the_bytes_read_before_conversion(client, monkeypatch):
    import base64

    from app.api.routes import topics_attachments
    from app.domain.library import service as library
    from app.domain.textfile import content_version

    pid, tid = _topic(client)
    path = "deck.pptx"
    original = b"PK\x03\x04source A"
    replacement = b"PK\x03\x04source B"
    shown = client.post(
        f"/topics/{tid}/shown",
        json={"path": path, "content_b64": base64.b64encode(original).decode()},
    )
    assert shown.status_code == 200, shown.text
    seen: list[bytes] = []
    reads: list[bytes] = []
    original_reader = topics_attachments.source_bytes

    async def read_once(*args, **kwargs):
        data = await original_reader(*args, **kwargs)
        reads.append(data)
        return data

    async def fake_render(data, path, endpoint, timeout=90.0):
        seen.append(data)
        if len(seen) == 1:
            library.write_room_file(uuid.UUID(pid), uuid.UUID(tid), path, replacement)
        return b"%PDF-1.7 same converted output"

    monkeypatch.setattr(settings, "office_render_endpoint", "http://renderer:8901")
    monkeypatch.setattr(topics_attachments, "source_bytes", read_once)
    monkeypatch.setattr(topics_attachments, "render_to_pdf", fake_render)
    first = client.get(f"/topics/{tid}/attachments/pdf", params={"path": path})
    assert first.status_code == 200, first.text
    assert first.content == b"%PDF-1.7 same converted output"
    assert first.headers["x-cheese-source-version"] == content_version(original)
    assert first.headers["cache-control"] == "no-store"
    second = client.get(f"/topics/{tid}/attachments/pdf", params={"path": path})
    assert second.status_code == 200, second.text
    assert second.content == first.content
    assert second.headers["x-cheese-source-version"] == content_version(replacement)
    assert second.headers["cache-control"] == "no-store"
    assert seen == [original, replacement]
    assert reads == [original, replacement]


def test_a_spreadsheet_is_never_sent_for_conversion(client):
    """Paginating a sheet breaks its columns apart and throws away the cell
    addresses — the only thing a reader can point at afterwards. The browser
    draws those from the original bytes instead."""
    import base64

    _pid, tid = _topic(client)
    client.post(
        f"/topics/{tid}/shown",
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
        f"/topics/{tid}/shown",
        json={
            "path": "报告.docx",
            "content_b64": base64.b64encode(b"PK\x03\x04").decode(),
        },
    )

    r = client.get(f"/topics/{tid}/attachments/pdf", params={"path": "报告.docx"})

    assert r.status_code == 503, r.text
    assert "文档预览" in r.json()["message"]


def test_a_sheet_is_served_as_a_web_page_where_no_pdf_is_made(client, monkeypatch):
    """The page is the second reading of a document, and a workbook is the case
    the PDF route deliberately has no answer for — which is why it is here."""
    import base64

    from app.api.routes import topics_attachments
    from app.domain.textfile import content_version

    _pid, tid = _topic(client)
    raw = b"PK\x03\x04a workbook"
    client.post(
        f"/topics/{tid}/shown",
        json={"path": "预算表.xlsx", "content_b64": base64.b64encode(raw).decode()},
    )

    seen: dict = {}

    async def fake_render(data, path, endpoint, timeout=90.0):
        seen["data"], seen["path"], seen["endpoint"] = data, path, endpoint
        return b"<!DOCTYPE html><html><head></head><body>x</body></html>"

    monkeypatch.setattr(settings, "office_render_endpoint", "http://renderer:8901")
    monkeypatch.setattr(topics_attachments, "render_to_html", fake_render)

    r = client.get(f"/topics/{tid}/attachments/html", params={"path": "预算表.xlsx"})

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    # The file's own bytes go to the renderer, not a path it cannot reach.
    assert seen["data"] == raw
    assert seen["path"] == "预算表.xlsx"
    assert r.headers["x-cheese-source-version"] == content_version(raw)
    assert r.headers["cache-control"] == "no-store"


def test_the_page_restricts_itself_because_a_header_would_not_follow(
    client, monkeypatch
):
    """The panel fetches these bytes with the Authorization header and hands
    them to a sandboxed frame; a response's CSP does not travel with bytes into
    that frame, so the page has to carry the policy itself."""
    import base64

    from app.api.routes import topics_attachments

    _pid, tid = _topic(client)
    client.post(
        f"/topics/{tid}/shown",
        json={
            "path": "报告.docx",
            "content_b64": base64.b64encode(b"PK\x03\x04").decode(),
        },
    )

    async def fake_render(data, path, endpoint, timeout=90.0):
        return (
            b"<!DOCTYPE html><html><head><title>t</title></head><body>x</body></html>"
        )

    monkeypatch.setattr(settings, "office_render_endpoint", "http://renderer:8901")
    monkeypatch.setattr(topics_attachments, "render_to_html", fake_render)

    r = client.get(f"/topics/{tid}/attachments/html", params={"path": "报告.docx"})

    assert r.status_code == 200, r.text
    page = r.content
    assert page.startswith(b"<!DOCTYPE html><html><head><meta http-equiv")
    # 页面自有脚本要放行：表格的多工作表标签靠它切换。
    assert b"script-src 'unsafe-inline'" in page
    # 外网不放行：这些页面会去字体 CDN、公式 CDN 和厂商主机取东西。
    assert b"default-src 'none'" in page
    assert b"https:" not in page.split(b'content="', 1)[1].split(b'"', 1)[0]

    header = r.headers["content-security-policy"]
    assert "sandbox allow-scripts" in header
    assert "allow-same-origin" not in header, "网页就在 API 这个源上，不能给它同源"
