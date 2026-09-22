"""The thin half of document preview: what gets sent, what comes back, and which
failures are the deployment's rather than the file's."""

import asyncio

import pytest

from app.domain.preview import office


@pytest.fixture(autouse=True)
def empty_cache():
    office._cache.clear()
    office._cache_bytes = 0
    office._inflight.clear()
    yield
    office._cache.clear()
    office._cache_bytes = 0
    office._inflight.clear()


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


async def test_the_cache_stays_bounded_by_bytes_too(client):
    """An entry ceiling alone is not a bound: thirty-two decks is a gigabyte."""
    each = office._CACHE_MAX_BYTES // 4

    for i in range(8):
        client.responses.append(_Response(200, b"%PDF-1.7" + b"x" * each))
        await office.render_to_pdf(f"doc-{i}".encode(), "a.docx", "http://r:8901")

    assert office._cache_bytes <= office._CACHE_MAX_BYTES
    assert len(office._cache) <= 4


async def test_one_body_never_answers_a_request_for_a_different_target(client):
    """The same bytes asked for as a document and as a sheet are two different
    answers. Keyed by the bytes alone, whichever was converted first would be
    handed to the other caller — a PDF given to a reader that draws workbooks.
    """
    client.responses.append(_Response(200, b"%PDF-1.7 as a document"))
    client.responses.append(_Response(200, b"PK\x03\x04as a sheet"))

    as_pdf = await office.convert(b"same bytes", "a.docx", "http://r:8901")
    as_xlsx = await office.convert(b"same bytes", "a.xls", "http://r:8901")

    assert as_pdf == b"%PDF-1.7 as a document"
    assert as_xlsx == b"PK\x03\x04as a sheet"
    assert client.calls[0]["url"].endswith("/render")
    assert client.calls[1]["url"].endswith("/convert")
    assert client.calls[1]["params"] == {"suffix": ".xls", "to": "xlsx"}


async def test_an_old_sheet_is_asked_for_as_a_workbook(client):
    """ExcelJS reads the modern format and nothing older, so `.xls` is the one
    sheet that does get converted — into a sheet, never into a PDF."""
    client.responses.append(_Response(200, b"PK\x03\x04book"))

    got = await office.convert(b"\xd0\xcf\x11\xe0old", "预算.xls", "http://r:8901")

    assert got == b"PK\x03\x04book"
    assert client.calls[0]["url"].endswith("/convert")
    assert client.calls[0]["params"] == {"suffix": ".xls", "to": "xlsx"}


async def test_a_body_of_the_wrong_kind_is_refused_per_target(client):
    """Each target is checked against its own magic bytes: a PDF answered for an
    `.xls` request is exactly the failure the guard exists for."""
    client.responses.append(_Response(200, b"%PDF-1.7 not a workbook"))

    with pytest.raises(office.OfficeRenderFailed):
        await office.convert(b"old", "预算.xls", "http://r:8901")


async def test_two_readers_of_one_document_cause_one_conversion(client, monkeypatch):
    """Two people opening the same item at the same moment is the ordinary case.
    Both miss the cache; without single-flight both pay for a conversion."""
    started = 0

    async def slow(raw, suffix, target, endpoint, timeout):
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        return b"%PDF-1.7 converted once"

    monkeypatch.setattr(office, "_ask", slow)

    results = await asyncio.gather(
        *[office.render_to_pdf(b"same", "a.docx", "http://r:8901") for _ in range(5)]
    )

    assert started == 1, "the waiters must join the conversion, not start their own"
    assert set(results) == {b"%PDF-1.7 converted once"}
    assert office._inflight == {}, "the key must not be left occupied"


async def test_a_failed_conversion_reaches_every_waiter_and_clears_itself(
    client, monkeypatch
):
    """A follower is told which file and why, and the key is freed — a key stuck
    holding a failed future would fail every later reader of that file."""
    started = 0

    async def broken(raw, suffix, target, endpoint, timeout):
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        raise office.OfficeRenderFailed("转换没有产出文件")

    monkeypatch.setattr(office, "_ask", broken)

    results = await asyncio.gather(
        *[office.render_to_pdf(b"same", "a.docx", "http://r:8901") for _ in range(3)],
        return_exceptions=True,
    )

    assert started == 1
    assert all(isinstance(r, office.OfficeRenderFailed) for r in results)
    assert office._inflight == {}

    # And the next reader gets a real attempt rather than the stale failure.
    monkeypatch.setattr(
        office, "_ask", lambda *a: asyncio.sleep(0, result=b"%PDF-1.7 better")
    )
    assert await office.render_to_pdf(b"same", "a.docx", "http://r:8901") == (
        b"%PDF-1.7 better"
    )


async def test_a_file_the_browser_draws_itself_is_handed_over_untouched(client):
    """A modern workbook, a CSV, an image: converting them would throw away what
    makes them readable. Nothing is asked of the renderer."""
    sheet = office.Preview(data=b"PK\x03\x04book", media_type="")

    shown = await office.preview(b"PK\x03\x04book", "预算.xlsx", "http://r:8901")

    assert shown.data == b"PK\x03\x04book"
    assert shown.media_type.endswith("spreadsheetml.sheet")
    assert client.calls == []
    assert sheet.data == b"PK\x03\x04book"


async def test_a_file_too_big_to_convert_is_refused_before_the_service_is_asked(
    client,
):
    """The service's own ceiling restated where the reader can read it, rather
    than a 413 that nobody maps to a sentence."""
    raw = b"P" * (office.MAX_PREVIEW_BYTES + 1)

    with pytest.raises(office.OfficeRenderFailed) as excinfo:
        await office.preview(raw, "大报告.docx", "http://r:8901")

    assert "10MB" in str(excinfo.value)
    assert client.calls == []


async def test_a_suffix_this_module_does_not_convert_cannot_come_back_as_a_pdf(
    client,
):
    """`render_to_pdf` is asked by callers that can only display pages. A
    workbook handed back to those would be a blank panel, so the refusal is
    here rather than at each call site."""
    with pytest.raises(office.OfficeRenderFailed):
        await office.render_to_pdf(b"PK\x03\x04book", "预算.xlsx", "http://r:8901")

    assert client.calls == []
