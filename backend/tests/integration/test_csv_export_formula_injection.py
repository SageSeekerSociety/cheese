"""学生填进报名表的东西，教师打开导出文件就执行。

Excel 和 WPS 会先把 RFC 4180 的引号剥掉，再对剩下的内容求值 —— 所以「加引号」
救不了报名时提交的 phone / email / 真名：学生把它们填成
``=cmd|'/C calc'!A1``、``+1+1``、``-1+1``、``@SUM(1+1)``、以 tab 开头的值，
教师导出名单后打开文件就中招。四份导出（tasks / publishers / participants
以及 deprecated 的 ``/{spaceId}/participants/export``）现在共用同一个渲染器，
这里盯的是真正落到盘上的字节（``response.content``），不是 API 的 JSON。

同一条规则的另一半：**只有公式才动**。普通值（引号、逗号、换行、中文、纯数字、
负数）必须和从前逐字节一样，否则下游解析器会看到不同的文件。
"""

import csv
import io
import re
import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)
from tests.integration.test_realname_sudo import _Owner

#: 学生能提交、且会被原样写进单元格的五个值。第一条就是审计里下载到的那个。
#: tab 开头的那条只能走 phone / email —— 真名和学号会被服务端 ``.strip()``。
FORMULA_PAYLOADS = (
    "=cmd|'/C calc'!A1",
    "+1+1",
    "-1+1",
    "@SUM(1+1)",
    "\t=cmd|'/C calc'!A1",
)

_DEADLINE_MS = int(time.time() * 1000) + 7 * 86400 * 1000


# ---------------------------------------------------------------------------
# 读文件的那一侧
# ---------------------------------------------------------------------------


def _rows(content: bytes) -> list[list[str]]:
    """导出文件按电子表格的读法展开。"""
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


def _cells(content: bytes) -> list[str]:
    return [cell for row in _rows(content) for cell in row]


def _column(content: bytes, name: str) -> list[str]:
    rows = _rows(content)
    index = rows[0].index(name)
    return [row[index] for row in rows[1:]]


def _opens_as_a_formula(cell: str) -> bool:
    """这一格会不会被 Excel/WPS 当成公式求值，而不是当文字显示。"""
    trimmed = cell.lstrip("\t\r\n ")
    if not trimmed or trimmed[0] not in "=+-@":
        return False
    # 纯负数是个值，不是公式。
    return re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", trimmed) is None


def _assert_nothing_opens_as_a_formula(content: bytes) -> None:
    offenders = [cell for cell in _cells(content) if _opens_as_a_formula(cell)]
    assert offenders == [], f"这些格子打开就会执行: {offenders}"


# ---------------------------------------------------------------------------
# 一间教室
# ---------------------------------------------------------------------------


def _board(user_client: UserCreator, client: TestClient) -> dict:
    admin = _Owner(user_client)
    resp = create_approved_space(
        client,
        json={
            "name": f"CSV export ({unique_int()})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=admin.headers,
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "admin": admin,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _member(board: dict, client: TestClient, person: _Owner) -> None:
    resp = client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": person.id},
        headers=board["admin"].headers,
    )
    assert resp.status_code == 201, resp.text


def _create_task(
    board: dict, client: TestClient, *, name: str, as_: _Owner | None = None
) -> int:
    publisher = as_ or board["admin"]
    resp = client.post(
        "/tasks",
        json={
            "name": name,
            "intro": "本周作业",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": _DEADLINE_MS,
        },
        headers=publisher.headers,
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    # 审题只有管理员能做，出题的人审不了自己发的题。
    approved = client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=board["admin"].headers,
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _set_identity(person: _Owner, client: TestClient, **fields: str) -> None:
    body = {
        "realName": fields.get("realName", "张三丰"),
        "studentId": fields.get("studentId", str(unique_int())),
        "grade": fields.get("grade", ""),
        "major": fields.get("major", ""),
        "className": fields.get("className", ""),
    }
    resp = person.put(client, body, person.sudo_ticket(client, "realname:update"))
    assert resp.status_code == 200, resp.text


def _apply(
    board: dict, client: TestClient, person: _Owner, *, phone: str, email: str
) -> None:
    """报名 —— phone / email 是学生自己填的，正是这两个字段。"""
    resp = client.post(
        f"/tasks/{board['task_id']}/participants",
        json={"phone": phone, "email": email},
        headers=person.headers,
    )
    assert resp.status_code == 200, resp.text


def _export(client: TestClient, board: dict, path: str) -> bytes:
    resp = client.get(
        f"/spaces/{board['space_id']}{path}",
        headers=board["admin"].headers,
    )
    assert resp.status_code == 200, (path, resp.text)
    return resp.content


# ---------------------------------------------------------------------------
# 学生可控的字段
# ---------------------------------------------------------------------------


@pytest.fixture
def classroom(user_client: UserCreator, api_client: TestClient) -> dict:
    """一间教室：两个学生，报名时交上来的全是公式。"""
    board = _board(user_client, api_client)
    board["task_id"] = _create_task(board, api_client, name="第一题")

    first, second = _Owner(user_client), _Owner(user_client)
    _member(board, api_client, first)
    _member(board, api_client, second)
    _set_identity(
        first, api_client, realName=FORMULA_PAYLOADS[2], studentId="2025000001"
    )
    _set_identity(second, api_client, studentId=FORMULA_PAYLOADS[3])
    _apply(
        board,
        api_client,
        first,
        phone=FORMULA_PAYLOADS[0],
        email=FORMULA_PAYLOADS[1],
    )
    _apply(
        board,
        api_client,
        second,
        phone=FORMULA_PAYLOADS[3],
        email=FORMULA_PAYLOADS[4],
    )
    board["students"] = (first, second)
    return board


def test_the_participants_export_writes_a_students_formulas_as_text(
    classroom: dict, api_client: TestClient
):
    content = _export(api_client, classroom, "/analytics/participants/export")

    # 每一条学生提交的值都在文件里，且都被中和成一个前缀撇号的文本格。
    for payload in FORMULA_PAYLOADS:
        assert ("'" + payload).encode() in content, payload

    assert _column(content, "Phone") == [
        "'" + FORMULA_PAYLOADS[0],
        "'" + FORMULA_PAYLOADS[3],
    ]
    assert _column(content, "Email") == [
        "'" + FORMULA_PAYLOADS[1],
        "'" + FORMULA_PAYLOADS[4],
    ]
    assert _column(content, "Real Name") == ["'" + FORMULA_PAYLOADS[2], "张三丰"]
    assert _column(content, "Student ID") == ["2025000001", "'" + FORMULA_PAYLOADS[3]]

    _assert_nothing_opens_as_a_formula(content)


def test_a_task_title_reaches_both_task_reports_as_text(
    classroom: dict, api_client: TestClient
):
    """出题的人也是学生，题目名字同样是他可控的。"""
    title = FORMULA_PAYLOADS[0]
    classroom["task_id"] = _create_task(
        classroom, api_client, name=title, as_=classroom["students"][0]
    )

    tasks = _export(api_client, classroom, "/analytics/tasks/export")
    assert "'" + title in _column(tasks, "Task Title")
    _assert_nothing_opens_as_a_formula(tasks)

    # deprecated 的那份导出用的是另一套实现，也必须被中和。
    legacy = _export(api_client, classroom, "/participants/export")
    assert "'" + title in _column(legacy, "taskName")
    _assert_nothing_opens_as_a_formula(legacy)


def test_a_publishers_nickname_reaches_the_publisher_report_as_text(
    classroom: dict, api_client: TestClient
):
    """昵称是用户自己改的，它落在发布者导出的 Publisher Name 一列。"""
    publisher = classroom["students"][0]
    nickname = FORMULA_PAYLOADS[0]
    changed = api_client.patch(
        f"/users/{publisher.id}",
        json={"nickname": nickname},
        headers=publisher.headers,
    )
    assert changed.status_code == 200, changed.text
    # 这位发布者得真的发一道题，否则它在发布者报表里没有一行。
    _create_task(classroom, api_client, name="他发的题", as_=publisher)

    publishers = _export(api_client, classroom, "/analytics/publishers/export")
    assert "'" + nickname in _column(publishers, "Publisher Name")
    _assert_nothing_opens_as_a_formula(publishers)

    tasks = _export(api_client, classroom, "/analytics/tasks/export")
    assert "'" + nickname in _column(tasks, "Creator")
    _assert_nothing_opens_as_a_formula(tasks)


# ---------------------------------------------------------------------------
# 普通值：一个字节都不许变
# ---------------------------------------------------------------------------


def test_ordinary_values_are_written_byte_for_byte_as_before(
    user_client: UserCreator, api_client: TestClient
):
    """只有公式才动。引号、逗号、换行、中文、纯数字、负数照旧。

    下面每一条期望值都是修复前的渲染器写出来的字节（RFC 4180 引号转义 +
    ``lineterminator=""``），并排在这里当锚：修复把它们改了，这条就红。
    """
    board = _board(user_client, api_client)
    board["task_id"] = _create_task(board, api_client, name="一道普通的题")
    student = _Owner(user_client)
    _member(board, api_client, student)
    _set_identity(
        student,
        api_client,
        realName='李"四", 同学',
        studentId="2025000123",
        grade="-5",
    )
    _apply(
        board,
        api_client,
        student,
        phone="13800138000",
        email="a@b.c\n第二行",
    )

    content = _export(api_client, board, "/analytics/participants/export")

    assert _column(content, "Real Name") == ['李"四", 同学']
    assert _column(content, "Student ID") == ["2025000123"]
    # 负数是个值：撇号会把它变成文本，也会改掉这一格的字节。
    assert _column(content, "Grade") == ["-5"]
    assert _column(content, "Phone") == ["13800138000"]
    # 换行仍旧靠引号包住，和修复前一样。
    assert _column(content, "Email") == ["a@b.c\n第二行"]

    # 连引号本身也是逐字节的：写进文件的是旧的转义结果，不是新写法。
    assert '"李""四"", 同学"'.encode() in content
    assert '"a@b.c\n第二行"'.encode() in content
    assert b"13800138000" in content

    _assert_nothing_opens_as_a_formula(content)
