"""The thin half of document preview: what gets sent, what comes back, and which
failures are the deployment's rather than the file's."""

import asyncio
import hashlib
import os

import pytest

from app.core.config import settings
from app.domain.preview import office


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    office._cache.clear()
    office._cache_bytes = 0
    yield
    office._cache.clear()
    office._cache_bytes = 0


class _Response:
    def __init__(self, status_code: int, content: bytes = b"", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Client:
    """Stands in for httpx.AsyncClient; records what each call was handed."""

    calls: list = []
    responses: list = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, params=None, content=None, headers=None):
        type(self).calls.append({"url": url, "params": params, "content": content})
        return type(self).responses.pop(0)


@pytest.fixture
def client(monkeypatch):
    _Client.calls = []
    _Client.responses = []
    monkeypatch.setattr(office.httpx, "AsyncClient", _Client)
    return _Client


async def test_a_word_file_goes_to_the_renderer_with_its_format_named(client):
    """soffice picks its import filter from the extension, so a `.docx` sent
    without one converts as nothing at all."""
    client.responses.append(_Response(200, b"%PDF-1.7 ok"))

    pdf = await office.render_to_pdf(b"PK\x03\x04doc", "out/报告.docx", "http://r:8901")

    assert pdf == b"%PDF-1.7 ok"
    assert client.calls[0]["params"] == {"suffix": ".docx"}
    assert client.calls[0]["content"] == b"PK\x03\x04doc"


async def test_the_same_bytes_are_converted_once(client):
    """The panel re-reads on a timer. Keyed by content, so a file 芝士 rewrote
    gets a fresh render with no invalidation to get wrong."""
    client.responses.append(_Response(200, b"%PDF-1.7 first"))

    first = await office.render_to_pdf(b"same", "a.docx", "http://r:8901")
    second = await office.render_to_pdf(b"same", "a.docx", "http://r:8901")

    assert first == second == b"%PDF-1.7 first"
    assert len(client.calls) == 1, "the second read must not reach the service"


async def test_rewritten_content_is_converted_again(client):
    client.responses.append(_Response(200, b"%PDF-1.7 before"))
    client.responses.append(_Response(200, b"%PDF-1.7 after"))

    await office.render_to_pdf(b"before", "a.docx", "http://r:8901")
    again = await office.render_to_pdf(b"after", "a.docx", "http://r:8901")

    assert again == b"%PDF-1.7 after"
    assert len(client.calls) == 2


async def test_a_deployment_with_no_renderer_is_told_apart_from_a_bad_file():
    """These two reach the reader as different sentences and must not collapse:
    one is about the deployment, the other about this file."""
    with pytest.raises(office.OfficeRenderUnavailable):
        await office.render_to_pdf(b"doc", "a.docx", None)


async def test_an_unreachable_renderer_is_the_deployment_s_problem(client):
    client.responses.append(_Response(500, payload={"error": "boom"}))

    with pytest.raises(office.OfficeRenderUnavailable):
        await office.render_to_pdf(b"doc", "a.docx", "http://r:8901")


async def test_a_refused_document_is_the_file_s_problem(client):
    client.responses.append(_Response(422, payload={"error": "转换没有产出文件"}))

    with pytest.raises(office.OfficeRenderFailed) as excinfo:
        await office.render_to_pdf(b"doc", "a.docx", "http://r:8901")

    # The service says why in a sentence; an HTTP code alone is not actionable.
    assert "转换没有产出文件" in str(excinfo.value)


# ---- The web view ---------------------------------------------------------


async def test_a_workbook_goes_to_the_page_endpoint(client):
    """The one format the two outputs disagree about: a sheet has no PDF route
    and does have a page, which is the whole reason it is in this set."""
    client.responses.append(_Response(200, b"<!DOCTYPE html><html>x</html>"))

    page = await office.render_to_html(b"PK\x03\x04book", "预算.xlsx", "http://r:8901")

    assert page.startswith(b"<!DOCTYPE html>")
    assert client.calls[0]["url"].endswith("/html")
    assert client.calls[0]["params"] == {"suffix": ".xlsx"}


async def test_a_document_the_pdf_path_takes_is_refused_by_the_page_path(client):
    """`.doc` converts to a PDF and has no page; refusing it here keeps the
    sentence about the format rather than about the renderer's HTTP code."""
    with pytest.raises(office.OfficeRenderFailed):
        await office.render_to_html(b"doc", "老报告.doc", "http://r:8901")

    assert client.calls == [], "nothing should have been sent"


async def test_the_same_bytes_cached_as_a_pdf_and_as_a_page_are_two_entries(client):
    """Keyed by the digest alone the page would answer for the PDF: the same
    bytes asked for twice, two different documents back."""
    client.responses.append(_Response(200, b"%PDF-1.7 printout"))
    client.responses.append(_Response(200, b"<!DOCTYPE html><html>page</html>"))
    raw = b"PK\x03\x04same"

    pdf = await office.render_to_pdf(raw, "a.docx", "http://r:8901")
    page = await office.render_to_html(raw, "a.docx", "http://r:8901")

    assert pdf == b"%PDF-1.7 printout"
    assert page.startswith(b"<!DOCTYPE html>")
    assert len(client.calls) == 2, "one answer served for both"
    assert len(office._cache) == 2

    # And each is still a hit the second time around.
    again_pdf = await office.render_to_pdf(raw, "a.docx", "http://r:8901")
    again_page = await office.render_to_html(raw, "a.docx", "http://r:8901")
    assert again_pdf == pdf and again_page == page
    assert len(client.calls) == 2


async def test_a_body_that_is_not_a_page_is_not_passed_off_as_one(client):
    """Something between here and the service answering 200 with a body that is
    not a page would otherwise reach the frame as the reader's document."""
    client.responses.append(_Response(200, b'{"ok": false, "error": "boom"}'))
    client.responses.append(_Response(200, b""))

    for _ in range(2):
        with pytest.raises(office.OfficeRenderFailed):
            await office.render_to_html(b"doc", "a.docx", "http://r:8901")


async def test_a_deployment_with_no_renderer_says_so_for_the_page_too():
    with pytest.raises(office.OfficeRenderUnavailable):
        await office.render_to_html(b"doc", "a.docx", None)


async def test_a_spreadsheet_is_refused_before_it_reaches_the_service(client):
    with pytest.raises(office.OfficeRenderFailed):
        await office.render_to_pdf(b"PK", "预算.xlsx", "http://r:8901")

    assert client.calls == [], "nothing should have been sent"


async def test_a_body_that_is_not_a_pdf_is_not_passed_off_as_one(client):
    """A proxy or captive portal answering 200 with an HTML page would otherwise
    reach the viewer as a document, which renders as a blank panel."""
    client.responses.append(_Response(200, b"<html>gateway</html>"))

    with pytest.raises(office.OfficeRenderFailed):
        await office.render_to_pdf(b"doc", "a.docx", "http://r:8901")


async def test_the_cache_stays_bounded(client):
    for i in range(office._CACHE_MAX_ENTRIES + 5):
        client.responses.append(_Response(200, b"%PDF-1.7 " + str(i).encode()))
        await office.render_to_pdf(f"doc-{i}".encode(), "a.docx", "http://r:8901")

    assert len(office._cache) <= office._CACHE_MAX_ENTRIES


def _forget_memory():
    """What a deploy does to the in-process cache."""
    office._cache.clear()
    office._cache_bytes = 0


async def test_a_conversion_survives_a_restart(client):
    """dev, 2026-09-27: every deploy emptied the memory cache and the first open
    of a Word file waited 3–3.6s for LibreOffice again."""
    client.responses.append(_Response(200, b"%PDF-1.7 kept"))
    await office.render_to_pdf(b"weekly", "w.docx", "http://r:8901")

    _forget_memory()
    again = await office.render_to_pdf(b"weekly", "w.docx", "http://r:8901")

    assert again == b"%PDF-1.7 kept"
    assert len(client.calls) == 1, (
        "the restart sent the same bytes to LibreOffice again"
    )


async def test_the_disk_copy_stays_bounded_oldest_first(client, monkeypatch):
    monkeypatch.setattr(office, "_DISK_MAX_BYTES", 40)
    for i in range(3):
        client.responses.append(
            _Response(200, b"%PDF-1.7 " + b"x" * 10 + bytes([48 + i]))
        )
        await office.render_to_pdf(f"doc-{i}".encode(), "a.docx", "http://r:8901")
        # Writes inside one clock tick share an mtime; give each a distinct age.
        key = hashlib.sha256(f"doc-{i}".encode()).hexdigest()
        written = office._disk_root() / f"{key}.pdf"
        if written.exists():
            os.utime(written, (1_000_000 + i * 10, 1_000_000 + i * 10))

    kept = list(office._disk_root().glob("*.pdf"))
    assert sum(f.stat().st_size for f in kept) <= 40
    _forget_memory()
    client.responses.append(_Response(200, b"%PDF-1.7 again"))
    await office.render_to_pdf(b"doc-0", "a.docx", "http://r:8901")
    assert len(client.calls) == 4, "the oldest was not the one let go"


async def _settle():
    await asyncio.gather(*office._warming)


async def test_a_saved_word_file_is_converted_before_anyone_opens_it(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "office_render_endpoint", "http://r:8901")
    client.responses.append(_Response(200, b"%PDF-1.7 early"))

    office.prewarm(b"fresh", "out/报告.docx")
    await _settle()
    opened = await office.render_to_pdf(b"fresh", "out/报告.docx", "http://r:8901")

    assert opened == b"%PDF-1.7 early"
    assert len(client.calls) == 1


async def test_prewarm_leaves_other_files_and_failures_alone(client, monkeypatch):
    monkeypatch.setattr(settings, "office_render_endpoint", "http://r:8901")
    office.prewarm(b"sheet", "data.xlsx")
    office.prewarm(b"text", "notes.md")
    await _settle()
    assert client.calls == []

    client.responses.append(_Response(500, payload={"error": "soffice crashed"}))
    office.prewarm(b"broken", "bad.docx")
    await _settle()  # swallowed: the save that triggered it is not affected
    assert len(client.calls) == 1


async def test_no_renderer_means_no_prewarm(client, monkeypatch):
    monkeypatch.setattr(settings, "office_render_endpoint", None)
    office.prewarm(b"fresh", "a.docx")
    await _settle()
    assert client.calls == []
