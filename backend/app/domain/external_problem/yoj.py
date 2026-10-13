"""YOJ 的抓取：登录，按题号把 AC 记录拉下来，把页面解析成行。

外站没有对外接口，这里读的是老师自己登录后看到的那些页面。只做三件事：登进去、
按题号翻页、把每行解析成 :class:`YojSubmission`。判谁做没做、写不写库，都不在这一层。

**地址与页面的形状（2026-10-12 实测）**

* 登录 ``POST /index.php/index/login/login2.html``，表单 ``username`` + ``passwd``，
  回 JSON ``{"status": 2, "uid": "20210129"}``：``2`` 教师、``3`` 管理员、``1`` 学生。
  学生看不到别人的提交，所以只有 2/3 能拿来同步。会话是 ThinkPHP 的 session，cookie
  名 ``uid``，大约五小时过期。
* ``POST /index.php/teacher/submissions/getPageInfo.html``，带同一套筛选参数，回
  JSON：``snum`` 是记录条数、``pidx`` 是页数。
* ``GET /index.php/teacher/submissions/index/p/<页>.html?prob=<题号>&status=Accepted``，
  一页 25 行，行里带着题号、状态、提交人的**学号**（``/teacher/user/detail/uid/<学号>
  .html``）和 unix 时间。

**两个容易做错的地方，都在这里兜住**

* 不加 ``class`` 参数。那个参数在服务端只是「我名下的学生」一个开关，不加反而能看到
  全班、甚至别的班做过这道题的人 —— 对上的人更多，而题号已经把量压到几十条。
* 题号在服务端的 SQL 是 ``problem_id = N or su_pname = 'N' or su_pname like '%N%'``，
  会多带出题名里含这个数的别的题，所以每行都要按解析出来的题号再对一次。

**会话过期不是 401，是 302 跳登录页。** 所以 ``follow_redirects`` 必须关掉：跟着跳
拿到的是登录页的 HTML，解析出来零行，而那会被读成「谁都没做出来」—— 这是这个功能
最坏的一种错法。跳转一律当成「要重登」，重登一次再来；还跳就是没成。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from lxml import html as lxml_html

#: 站点的地址写死在这里而不是配置里：换站点是换一个客户端，不是运维改一个值。
BASE_URL = "http://yoj.ruc.edu.cn"

LOGIN_PATH = "/index.php/index/login/login2.html"
PAGE_INFO_PATH = "/index.php/teacher/submissions/getPageInfo.html"
LIST_PATH = "/index.php/teacher/submissions/index/p/{page}.html"

#: 一页多少行是站点定的（``data-page-size``），不是我们请求的参数。
PAGE_SIZE = 25

#: 一页一轮请求，一次同步最多翻这么多页（2000 行）。超了报错而不是截断：截断会让
#: 排在后面的人被算成「没过」，那是个看起来对、其实错的答案。
MAX_PAGES = 80

DEFAULT_TIMEOUT_S = 20.0

#: 提交人那一格的链接里就是学号，比格子里的「姓名(学号)」稳（姓名里可以有括号）。
_PNO = re.compile(r"/teacher/problem/detail/pno/([^/]+)\.html")
_UID = re.compile(r"uid/([^/]+)\.html")


@dataclass(frozen=True)
class YojSubmission:
    """外站上一行 AC：谁、哪道题、什么时候 AC 的。"""

    student_no: str
    problem_no: str
    accepted_at: datetime


class YojError(Exception):
    """没成。``kind`` 是机器认的那一档，``detail`` 是现场的原文。

    ``kind`` 取值：``unreachable``（连不上）、``login_failed``、``not_a_teacher``、
    ``http``、``unreadable``（页面/JSON 不是认得的样子）、``too_many``（页数超上限）。

    **这里不写给人看的句子。** 说给老师听的那句由界面按 ``kind`` 说（它要按读者选的
    语言来），``detail`` 是缩在小字里的现场原文 —— 一个数、一个状态码，或者库自己
    吐出来的那句话。理由是 `tests/unit/test_a_refusal_is_said_with_a_key.py`：写死在
    异常里的句子没有 key，换个语言读的人就只能看到原文。
    """

    def __init__(self, kind: str, detail: object = "") -> None:
        super().__init__(kind)
        self.kind = kind
        self.detail = str(detail)


def parse_accepted_rows(markup: bytes | str) -> list[YojSubmission]:
    """一页 HTML 里的 AC 行。认不出的行跳过，不抛。

    解析不出任何行不一定代表没人 AC —— 也可能是站点改了版。判断这件事是调用方的
    事（拿 ``snum`` 和这里的条数比），因为只有它知道站点说这页该有多少条。
    """
    tree = lxml_html.fromstring(markup)
    rows: list[YojSubmission] = []
    for tr in tree.xpath("//tbody/tr"):
        hrefs = tr.xpath(".//a/@href")
        problem_no = next((m.group(1) for h in hrefs if (m := _PNO.search(h))), None)
        student_no = next((m.group(1) for h in hrefs if (m := _UID.search(h))), None)
        stamp = tr.xpath("string(.//td[contains(@class, 'stime')])").strip()
        status = "".join(tr.xpath(".//span[@class='status']//text()")).strip()
        if not problem_no or not student_no or not stamp.isdigit():
            continue
        if status != "Accepted":
            continue
        rows.append(
            YojSubmission(
                student_no=student_no,
                problem_no=problem_no,
                accepted_at=datetime.fromtimestamp(int(stamp), UTC),
            )
        )
    return rows


def _json_of(response: httpx.Response) -> dict:
    """回的东西当 JSON 读。站点会在 JSON 前面放一个 BOM，所以先剥掉。"""
    try:
        body = json.loads(response.text.lstrip("﻿").strip())
    except ValueError as exc:
        raise YojError("unreadable", exc) from exc
    if not isinstance(body, dict):
        raise YojError("unreadable")
    return body


class YojClient:
    """一名老师的一次会话。用完 ``aclose()``（或者用 ``async with``）。"""

    def __init__(
        self,
        *,
        username: str,
        password: str,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._username = username
        self._password = password
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=timeout,
            # 见模块头：会话过期是一跳，不是 401。
            follow_redirects=False,
        )
        self._signed_in = False

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> YojClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _sign_in(self) -> None:
        try:
            response = await self._http.post(
                LOGIN_PATH,
                data={"username": self._username, "passwd": self._password},
            )
        except httpx.HTTPError as exc:
            raise YojError("unreachable", exc) from exc
        if response.status_code != 200:
            raise YojError("login_failed", response.status_code)
        body = _json_of(response)
        status = int(body.get("status") or 0)
        if status not in (2, 3):
            raise YojError("not_a_teacher", status)
        self._signed_in = True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """一次请求。跳转 = 会话过期，重登一次再来；再跳就是没成。"""
        for attempt in (0, 1):
            if not self._signed_in:
                await self._sign_in()
            try:
                response = await self._http.request(
                    method, path, data=data, params=params
                )
            except httpx.HTTPError as exc:
                raise YojError("unreachable", exc) from exc
            if response.status_code == 200:
                return response
            if response.is_redirect and attempt == 0:
                self._signed_in = False
                continue
            raise YojError("http", response.status_code)
        raise YojError("http")

    async def _page_count(self, problem_no: str) -> tuple[int, int]:
        """这道题有多少条 AC、多少页。"""
        response = await self._request(
            "POST",
            PAGE_INFO_PATH,
            data={"prob": problem_no, "status": "Accepted", "p": "1"},
        )
        body = _json_of(response)
        records = int(body.get("snum") or 0)
        pages = int(body.get("pidx") or 0)
        return records, pages

    async def accepted_submissions(self, problem_no: str) -> list[YojSubmission]:
        """这道题上所有 AC 记录，一个人只留最早那一次。

        返回的是**题号严格相等**的那些行 —— 服务端那个 ``like '%N%'`` 会多带别人的题
        进来（见模块头）。
        """
        records, pages = await self._page_count(problem_no)
        if pages > MAX_PAGES:
            raise YojError("too_many", pages)
        earliest: dict[str, YojSubmission] = {}
        parsed = 0
        for page in range(1, pages + 1):
            response = await self._request(
                "GET",
                LIST_PATH.format(page=page),
                params={"prob": problem_no, "status": "Accepted"},
            )
            rows = parse_accepted_rows(response.content)
            parsed += len(rows)
            for row in rows:
                if row.problem_no != problem_no:
                    continue
                kept = earliest.get(row.student_no)
                if kept is None or row.accepted_at < kept.accepted_at:
                    earliest[row.student_no] = row
        if records > 0 and parsed == 0:
            # 站点说这题有记录，我们一行也没认出来 —— 页面改了版，不是没人过。
            # 报错让人去看，总好过把「全都没过」写进名单。
            raise YojError("unreadable", records)
        return [earliest[key] for key in sorted(earliest)]
