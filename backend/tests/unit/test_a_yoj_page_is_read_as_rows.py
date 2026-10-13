"""外站那一层：一页 HTML 读成哪些行，一趟翻页什么时候算没成。

这里驱动的是**真的** ``YojClient``，只把 HTTP 传输换成假站点（``httpx.MockTransport``）
—— 解析、重登、翻页上限这些判断都在被测的那一侧真跑，不像 ``service`` 层的用例那样
把它们替掉。路径写成字面量而不引模块里的常量：那三条是和站点之间的约定，站点改了版本
这里就该红。

假站点只认三条真实路径，别的请求一律抛出来，免得用例本身写错了还看着像绿的。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.external_problem.yoj import YojClient, YojError, parse_accepted_rows

#: 站点给的 unix 时间戳；用例只拿它和对出来的时刻比。
AT = 1_700_000_000

LOGIN_PATH = "/index.php/index/login/login2.html"
PAGE_INFO_PATH = "/index.php/teacher/submissions/getPageInfo.html"
LIST_PREFIX = "/index.php/teacher/submissions/index/p/"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _row(
    problem_no: str,
    student_no: str,
    *,
    at: int = AT,
    status: str = "Accepted",
    student_link: bool = True,
) -> str:
    """一行提交记录。``student_link=False`` 造一个读不出提交人的行。"""
    who = (
        '<a href="/index.php/teacher/user/detail/uid/'
        f'{student_no}.html">谁({student_no})</a>'
        if student_link
        else f"谁({student_no})"
    )
    return (
        "<tr>"
        f'<td><a href="/index.php/teacher/problem/detail/pno/{problem_no}.html">'
        f"{problem_no}</a> 题名</td>"
        f"<td>{who}</td>"
        f'<td><span class="status">{status}</span></td>'
        f'<td class="stime">{at}</td>'
        "</tr>"
    )


def _page(*rows: str) -> bytes:
    return ("<table><tbody>" + "".join(rows) + "</tbody></table>").encode()


class _FakeSite:
    """一个假 YOJ。认三条路径，记下每条被要了几次，最后一趟登录的请求体留着。"""

    def __init__(
        self,
        *,
        login: object = 2,
        records: int = 0,
        pages: int = 0,
        bodies: list[bytes] | None = None,
        redirects_first: int = 0,
    ) -> None:
        self.login_count = 0
        self.page_info_count = 0
        self.list_count = 0
        self.last_login_body = b""
        self._login = login
        self._records = records
        self._pages = pages
        self._bodies = list(bodies or [])
        #: 前几次列表请求回 302（会话过期的样子），之后正常。
        self._redirects_first = redirects_first

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == LOGIN_PATH:
            self.login_count += 1
            self.last_login_body = request.content
            if self._login == "http":
                return httpx.Response(503)
            body = json.dumps({"status": self._login, "uid": "20210129"})
            # 站点在 JSON 前面放了一个 BOM。
            return httpx.Response(200, content=b"\xef\xbb\xbf" + body.encode())
        if path == PAGE_INFO_PATH:
            self.page_info_count += 1
            return httpx.Response(
                200, json={"snum": self._records, "pidx": self._pages}
            )
        if path.startswith(LIST_PREFIX):
            self.list_count += 1
            if self._redirects_first > 0:
                self._redirects_first -= 1
                return httpx.Response(
                    302, headers={"location": "/index.php/index/login"}
                )
            # 按地址里那一页取，不按第几次请求取 —— 一次重试会让两者错开。
            page = int(path[len(LIST_PREFIX) :].removesuffix(".html"))
            index = page - 1
            body = self._bodies[index] if index < len(self._bodies) else _page()
            return httpx.Response(200, content=body)
        raise AssertionError(f"unexpected request: {request.url}")


def _client(site: _FakeSite) -> YojClient:
    return YojClient(
        username="20210129",
        password="pw",
        base_url="http://yoj.test",
        transport=httpx.MockTransport(site),
    )


# ---- 一页 HTML 读成哪些行 ----------------------------------------------------


def test_a_row_says_who_did_which_problem_and_when():
    rows = parse_accepted_rows(_page(_row("1234", "20210129")))

    assert [(r.problem_no, r.student_no) for r in rows] == [("1234", "20210129")]
    assert rows[0].accepted_at.timestamp() == AT


def test_a_row_that_is_not_accepted_is_not_a_row():
    markup = _page(_row("1234", "20210129", status="Wrong Answer"))
    assert parse_accepted_rows(markup) == []


def test_a_row_whose_submitter_cannot_be_read_is_skipped():
    markup = _page(_row("1234", "20210129", student_link=False))
    assert parse_accepted_rows(markup) == []


def test_a_row_whose_time_is_not_a_number_is_skipped():
    markup = _page(_row("1234", "20210129", at="3 分钟前"))
    assert parse_accepted_rows(markup) == []


# ---- 一趟翻页 -----------------------------------------------------------------


async def test_the_login_is_a_form_post_of_the_account():
    site = _FakeSite(records=1, pages=1, bodies=[_page(_row("1234", "20210129"))])
    async with _client(site) as client:
        await client.accepted_submissions("1234")

    assert site.login_count == 1
    assert site.last_login_body == b"username=20210129&passwd=pw"


async def test_an_expired_session_is_signed_in_again():
    """会话过期是 302，不是 401 —— 跟着跳会拿到登录页，读出来零行，那会被当成
    「谁都没做出来」。这里断的是一次重登之后拿到的是真的那一页。"""
    site = _FakeSite(
        records=1, pages=1, bodies=[_page(_row("1234", "20210129"))], redirects_first=1
    )
    async with _client(site) as client:
        rows = await client.accepted_submissions("1234")

    assert [r.student_no for r in rows] == ["20210129"]
    assert site.login_count == 2


async def test_a_second_redirect_is_a_failure_not_an_empty_list():
    site = _FakeSite(records=1, pages=1, redirects_first=2)
    async with _client(site) as client:
        with pytest.raises(YojError) as caught:
            await client.accepted_submissions("1234")

    assert caught.value.kind == "http"


async def test_a_problem_the_server_dragged_in_is_not_this_problem():
    """服务端那条件是 ``problem_id = N or su_pname like '%N%'``，会多带别的题进来。"""
    site = _FakeSite(
        records=2, pages=1, bodies=[_page(_row("12", "a"), _row("1212", "b"))]
    )
    async with _client(site) as client:
        rows = await client.accepted_submissions("12")

    assert [r.problem_no for r in rows] == ["12"]


async def test_only_the_earliest_accept_of_a_person_is_kept():
    site = _FakeSite(
        records=2,
        pages=1,
        bodies=[
            _page(
                _row("1234", "20210129", at=AT),
                _row("1234", "20210129", at=AT - 600),
            )
        ],
    )
    async with _client(site) as client:
        rows = await client.accepted_submissions("1234")

    assert [r.accepted_at.timestamp() for r in rows] == [AT - 600]


async def test_a_student_login_is_not_a_teacher():
    site = _FakeSite(login=1)
    async with _client(site) as client:
        with pytest.raises(YojError) as caught:
            await client.accepted_submissions("1234")

    assert caught.value.kind == "not_a_teacher"


async def test_records_we_cannot_read_are_a_failure_not_an_empty_list():
    """站点说这题有记录，我们一行也没认出来 —— 是页面改了版，不是没人过。"""
    site = _FakeSite(records=7, pages=1, bodies=[_page("<tr><td>换了版的行</td></tr>")])
    async with _client(site) as client:
        with pytest.raises(YojError) as caught:
            await client.accepted_submissions("1234")

    assert caught.value.kind == "unreadable"


async def test_too_many_pages_is_a_failure_rather_than_a_truncated_list():
    """截断会让排在后面的人被算成「没过」—— 看起来对、其实错。"""
    site = _FakeSite(records=99999, pages=81)
    async with _client(site) as client:
        with pytest.raises(YojError) as caught:
            await client.accepted_submissions("1234")

    assert caught.value.kind == "too_many"
    assert site.list_count == 0
