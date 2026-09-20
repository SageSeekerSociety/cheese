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


# ---- 表格的投影（.xls → xlsx）-------------------------------------------
# 与 PDF 那条路分开测，因为它问的是另一个端点、要的是另一种东西：一份读者能读出
# 单元格的工作簿，而不是一页画。上游的合同（服务会做哪些转换）在 CONVERTIBLE 里。


async def test_a_legacy_spreadsheet_is_asked_for_as_a_workbook(client):
    """soffice 按扩展名挑导入过滤器，而目标格式要明说：`/render` 只做 PDF。"""
    client.responses.append(_Response(200, b"PK\x03\x04 workbook"))

    book = await office.project_to_xlsx(
        b"\xd0\xcf\x11\xe0legacy", "out/预算表.xls", "http://r:8901"
    )

    assert book == b"PK\x03\x04 workbook"
    assert client.calls[0]["url"] == "http://r:8901/convert"
    assert client.calls[0]["params"] == {"suffix": ".xls", "to": "xlsx"}
    assert client.calls[0]["content"] == b"\xd0\xcf\x11\xe0legacy"


async def test_a_workbook_the_reader_can_read_is_refused_before_it_is_sent(client):
    """`.xlsx` 走原始字节那条路；到这里来问的是一个不该被问的问题。"""
    with pytest.raises(office.OfficeRenderFailed):
        await office.project_to_xlsx(b"PK", "预算.xlsx", "http://r:8901")

    assert client.calls == [], "nothing should have been sent"


async def test_a_projection_that_is_not_a_zip_is_not_passed_off_as_a_workbook(client):
    """代理或登录页回一个 200 的 HTML，否则会以「表格」的名义交给阅读器，
    读者看到的是一个空白的方格。"""
    client.responses.append(_Response(200, b"<html>gateway</html>"))

    with pytest.raises(office.OfficeRenderFailed):
        await office.project_to_xlsx(b"legacy", "预算.xls", "http://r:8901")


async def test_a_deployment_without_a_renderer_cannot_project_either():
    with pytest.raises(office.OfficeRenderUnavailable):
        await office.project_to_xlsx(b"legacy", "预算.xls", None)


async def test_an_unreachable_renderer_is_the_deployment_s_problem_here_too(client):
    client.responses.append(_Response(503, payload={"error": "renderer down"}))

    with pytest.raises(office.OfficeRenderUnavailable):
        await office.project_to_xlsx(b"legacy", "预算.xls", "http://r:8901")


async def test_a_projection_is_cached_by_content(client):
    client.responses.append(_Response(200, b"PK\x03\x04 first"))

    first = await office.project_to_xlsx(b"same", "预算.xls", "http://r:8901")
    second = await office.project_to_xlsx(b"same", "预算.xls", "http://r:8901")

    assert first == second == b"PK\x03\x04 first"
    assert len(client.calls) == 1, "the panel re-reads on a timer"


async def test_the_same_bytes_asked_for_two_ways_do_not_answer_for_each_other(client):
    """缓存键里有目标格式。同一串字节既可能是一份要被投影的 `.xls`，也可能是别的
    什么——按内容哈希取，第二次就会拿到第一次的那份，而且没有任何地方会报错。"""
    client.responses.append(_Response(200, b"%PDF-1.7 pages"))
    client.responses.append(_Response(200, b"PK\x03\x04 cells"))

    pdf = await office.render_to_pdf(b"same", "a.docx", "http://r:8901")
    book = await office.project_to_xlsx(b"same", "a.xls", "http://r:8901")

    assert pdf == b"%PDF-1.7 pages"
    assert book == b"PK\x03\x04 cells"
    assert len(client.calls) == 2
