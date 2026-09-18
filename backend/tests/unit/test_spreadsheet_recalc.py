"""重算一份表格：送出去什么、读回来什么，以及算不出来的格怎么报。

「公式没重算」是这条路上唯一会静默交付错数字的失败，所以这里盯两件事：请求带上了格式
（soffice 靠扩展名选导入过滤器，不带就当成别的格式打开），以及错误值被认出来并按
`Sheet1!B7` 这种人能在 Excel 里找到的坐标报出去。
"""

import zipfile
from io import BytesIO

import pytest

from app.domain.documents import spreadsheet

WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="预算" sheetId="1" r:id="rId1"/>'
    '<sheet name="明细" sheetId="2" r:id="rId2"/></sheets></workbook>'
)

RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="worksheets/sheet1.xml"'
    ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
    '<Relationship Id="rId2" Target="worksheets/sheet2.xml"'
    ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
    "</Relationships>"
)


def sheet(*cells: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1">{"".join(cells)}</row></sheetData></worksheet>'
    )


def book(first: str, second: str = "") -> bytes:
    raw = BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", RELS)
        z.writestr("xl/worksheets/sheet1.xml", first)
        z.writestr("xl/worksheets/sheet2.xml", second or sheet())
    return raw.getvalue()


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
    monkeypatch.setattr(spreadsheet.httpx, "AsyncClient", _Client)
    return _Client


def test_an_error_value_is_reported_where_a_person_can_find_it():
    """报 `xl/worksheets/sheet1.xml` 等于没报——用户在 Excel 里找不到这个名字。"""
    bad = spreadsheet.cells_that_did_not_compute(
        book(
            sheet(
                '<c r="A1" t="n"><f>SUM(B1:B2)</f><v>5</v></c>',
                '<c r="B7" t="e"><f>A1/0</f><v>#DIV/0!</v></c>',
            )
        )
    )
    assert [c.address for c in bad] == ["预算!B7"]
    assert bad[0].value == "#DIV/0!"


def test_a_sheet_that_computed_reports_nothing():
    """交付前这张单子要是空的，所以「空」必须真的意味着没有错误。"""
    assert (
        spreadsheet.cells_that_did_not_compute(
            book(sheet('<c r="A1" t="n"><f>SUM(B1:B2)</f><v>5</v></c>'))
        )
        == []
    )


def test_every_sheet_is_looked_at():
    """错误藏在第二张表里同样是错的交付。"""
    bad = spreadsheet.cells_that_did_not_compute(
        book(
            sheet('<c r="A1" t="n"><v>1</v></c>'),
            sheet('<c r="C3" t="e"><f>VLOOKUP(1,X:X,1,0)</f><v>#N/A</v></c>'),
        )
    )
    assert [c.address for c in bad] == ["明细!C3"]


def test_text_that_merely_looks_like_an_error_is_not_one():
    """有人把「#REF!」当成说明写进单元格，那不是算不出来。"""
    assert (
        spreadsheet.cells_that_did_not_compute(
            book(sheet('<c r="A1" t="s"><v>#REF!</v></c>'))
        )
        == []
    )


async def test_the_workbook_goes_out_with_its_format_named(client):
    """soffice 按扩展名选导入过滤器，不带扩展名它会当成另一种格式打开。"""
    good = book(sheet('<c r="A1" t="n"><f>SUM(B1:B2)</f><v>5</v></c>'))
    client.responses.append(_Response(200, good))
    returned, bad = await spreadsheet.recalculate(
        b"PK original", "报表/预算表.xlsx", "http://render:8901/"
    )
    assert client.calls[0]["url"] == "http://render:8901/recalc"
    assert client.calls[0]["params"] == {"suffix": ".xlsx"}
    assert client.calls[0]["content"] == b"PK original"
    assert returned == good
    assert bad == []


async def test_a_format_without_formulas_is_refused_before_the_call(client):
    """`.docx` 里没有公式可重算，这是提错了，不该占用那台机器。"""
    with pytest.raises(spreadsheet.SpreadsheetRecalcFailed):
        await spreadsheet.recalculate(b"PK", "报告.docx", "http://render:8901")
    assert client.calls == []


async def test_a_deployment_without_the_service_says_so():
    """没有这个服务和这个文件不能重算是两句话，调用方要分开处理。"""
    with pytest.raises(spreadsheet.SpreadsheetRecalcUnavailable):
        await spreadsheet.recalculate(b"PK", "预算表.xlsx", None)


async def test_the_services_own_sentence_is_passed_through(client):
    """服务自己说得清为什么，转成 HTTP 码等于把这句话丢了。"""
    client.responses.append(_Response(422, payload={"error": "这个工作簿有密码"}))
    with pytest.raises(spreadsheet.SpreadsheetRecalcFailed, match="密码"):
        await spreadsheet.recalculate(b"PK", "预算表.xlsx", "http://render:8901")


async def test_a_server_error_is_the_deployments_problem_not_the_files(client):
    """5xx 重试有意义，422 重试没有意义——两者不能报成同一件事。"""
    client.responses.append(_Response(503, payload={"error": "忙"}))
    with pytest.raises(spreadsheet.SpreadsheetRecalcUnavailable):
        await spreadsheet.recalculate(b"PK", "预算表.xlsx", "http://render:8901")


async def test_something_that_is_not_a_workbook_is_not_written_back(client):
    """把非 xlsx 写回原路径会毁掉用户的表格，而且过程里不报错。"""
    client.responses.append(_Response(200, b"<html>error page</html>"))
    with pytest.raises(spreadsheet.SpreadsheetRecalcFailed):
        await spreadsheet.recalculate(b"PK", "预算表.xlsx", "http://render:8901")


async def test_a_macro_workbook_is_refused_rather_than_stripped(client):
    """重算写回来的是纯 xlsx，`.xlsm` 走这条路会把宏丢掉，而且文件大小正常、不报错。"""
    with pytest.raises(spreadsheet.SpreadsheetRecalcFailed):
        await spreadsheet.recalculate(b"PK", "带宏的表.xlsm", "http://render:8901")
    assert client.calls == []


async def test_a_renderer_too_old_for_this_endpoint_is_not_blamed_on_the_file(
    client,
):
    """404 说的是那台服务还没有这个接口，不是这份表格有问题——两句话指向不同的人。"""
    client.responses.append(_Response(404, payload=None))
    with pytest.raises(spreadsheet.SpreadsheetRecalcUnavailable):
        await spreadsheet.recalculate(b"PK", "预算表.xlsx", "http://render:8901")
