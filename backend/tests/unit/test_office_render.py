"""The thin half of document preview: what gets sent, what comes back, and which
failures are the deployment's rather than the file's."""

import pytest

from app.domain.preview import office


@pytest.fixture(autouse=True)
def empty_cache():
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
