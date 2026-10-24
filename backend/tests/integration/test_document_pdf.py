"""A document printed as a PDF.

The page itself is drawn by the reader's browser, which is the only place the
document's styles, blocks and pictures are all present; this route hands that
page to the platform's printing browser and returns what it printed. The door
is the document's own: whoever may read it may print it, and nobody else.
"""

import base64

import httpx

import app.api.routes.document_print as route
from app.core.config import settings
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    session_auth_headers,
)
from tests.support.living_doc import document_of

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n%%EOF\n"
SOMETHING_ELSE = b"<html>not a pdf</html>"


class Renderer:
    """The printing browser, without the browser: it records the page it was
    handed and answers with a PDF (or with whatever a test tells it to)."""

    def __init__(self, made: bytes = PDF, ok: bool = True, status: int = 200):
        self.calls: list[dict] = []
        self.ok = ok
        self.status = status
        self.made = made

    async def post(self, url: str, json: dict | None = None) -> httpx.Response:
        self.calls.append({"url": url, "body": json or {}})
        return httpx.Response(
            self.status,
            json={
                "ok": self.ok,
                "pdf_b64": base64.b64encode(self.made).decode(),
            },
        )


def _through(
    monkeypatch, renderer: Renderer, endpoint: str | None = "http://render.test"
):
    """The route talks to ``renderer`` instead of a printing browser."""
    monkeypatch.setattr(settings, "fetch_browser_endpoint", endpoint)

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return renderer

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(route.httpx, "AsyncClient", Client)
    return renderer


def _project(client) -> str:
    for who in ("alice", "bob", "dave", "mallory"):
        seed_user(client, who)
    pid = post_project(client, json={"name": "P"}, owner="dave").json()["data"]["id"]
    for handle in ("alice", "bob"):
        join_project_team(client, pid, handle)
    return pid


def _document(client) -> tuple[str, str]:
    """alice's task and the living document it keeps."""
    pid = _project(client)
    room = client.post(
        "/topics",
        json={"project_id": pid, "title": "频道", "members_only": False},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    task = open_task(client, room, owner="alice")["id"]
    return task, document_of(client, task, headers=session_auth_headers("alice"))


def _print(client, document: str, who: str, html: str = "<main>正文</main>"):
    return client.post(
        f"/documents/{document}/pdf",
        json={"html": html},
        headers=session_auth_headers(who),
    )


def test_a_reader_prints_the_document_they_can_read(client, monkeypatch):
    _, document = _document(client)
    printer = _through(monkeypatch, Renderer())

    printed = _print(client, document, "alice", "<main>本周进展</main>")

    assert printed.status_code == 200, printed.text
    assert printed.content == PDF
    assert printed.headers["content-type"] == "application/pdf"
    # The page that was handed over is the reader's, and nothing re-drew it.
    assert printer.calls[0]["url"] == "http://render.test/print"
    assert printer.calls[0]["body"]["html"] == "<main>本周进展</main>"
    # A4 with the printer's own margins.
    assert printer.calls[0]["body"]["margin"] == "16mm 18mm"


def test_someone_who_cannot_read_the_document_cannot_print_it(client, monkeypatch):
    _, document = _document(client)
    printer = _through(monkeypatch, Renderer())

    refused = _print(client, document, "mallory")

    assert refused.status_code != 200
    assert printer.calls == []


def test_a_deployment_without_a_printer_says_so(client, monkeypatch):
    _, document = _document(client)
    printer = _through(monkeypatch, Renderer(), endpoint=None)

    refused = _print(client, document, "alice")

    assert refused.status_code == 503
    assert printer.calls == []


def test_a_renderer_that_answers_something_other_than_a_pdf_is_refused(
    client, monkeypatch
):
    _, document = _document(client)
    _through(monkeypatch, Renderer(made=SOMETHING_ELSE))

    refused = _print(client, document, "alice")

    # An error page under a PDF's name must not reach a download.
    assert refused.status_code == 503


def test_a_renderer_that_does_not_print_at_all_is_refused(client, monkeypatch):
    _, document = _document(client)
    _through(monkeypatch, Renderer(ok=False))

    refused = _print(client, document, "alice")

    assert refused.status_code == 503
