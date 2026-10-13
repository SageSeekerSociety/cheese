"""外站那两条面上的四扇门和两条读路径。

任务侧由**出题人或板管理员**开（``may_teach_task``）—— 这里按浏览器会收到的状态码
逐条断，不看实现。平台管理员那一侧只有平台管理员进得去，密码只写不回显。

同步那一趟用假外站（``monkeypatch`` 掉 ``service._yoj_client``）：真的抓取在
``tests/unit/test_a_yoj_page_is_read_as_rows.py``，真的落库在
``tests/integration/test_external_problem_sync.py``。这里要证的是**接得对不对**：
返回体里的 ``participantId`` 是不是名单接口发出去的那个 id。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.external_problem import service as service_module
from app.domain.external_problem.schemas import (
    ExternalProblemAccountOut,
    ExternalProblemResultOut,
    TaskExternalProblemOut,
)
from app.domain.external_problem.service import account_view, binding_view, result_view
from app.domain.external_problem.yoj import YojSubmission
from tests.integration.conftest import UserCreator, session_auth_headers
from tests.integration.test_a_board_teacher import (
    _auth,
    _create_task,
    _login,
    _make_admin,
    _make_member,
    _new_board,
)

AT = 1_700_000_000


class _FakeYoj:
    """假外站：谁 AC 过由用例给的那串学号定。"""

    def __init__(self, solved: dict[str, str] | None = None) -> None:
        #: 学号 -> 题号，都是「这个人在这个题号上过过」。
        self._solved = solved or {}

    async def __aenter__(self) -> _FakeYoj:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def accepted_submissions(self, problem_no: str) -> list[YojSubmission]:
        return [
            YojSubmission(
                student_no=student_no,
                problem_no=problem_no,
                accepted_at=datetime.fromtimestamp(AT, UTC),
            )
            for student_no, pno in self._solved.items()
            if pno == problem_no
        ]


def _board_with_a_student(
    api_client: TestClient, user_client: UserCreator, *, name: str
) -> dict:
    """一块板、一道过审的题、一位领了题的成员，外加一位板管理员和一位旁观成员。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name=name)

    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)

    other = user_client.create_user()
    other_token = _login(user_client, api_client, other)
    assert _make_member(api_client, board, other.user_id).status_code == 201

    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text
    joined = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": student.user_id},
        json={},
        headers=_auth(student_token),
    )
    assert joined.status_code == 200, joined.text
    participant_id = joined.json()["data"]["participant"]["id"]

    roster = api_client.get(
        f"/tasks/{task_id}/participants", headers=_auth(board["creator_token"])
    )
    assert roster.status_code == 200, roster.text

    return {
        **board,
        "task_id": task_id,
        "teacher_token": teacher_token,
        "student": student,
        "student_token": student_token,
        "other_token": other_token,
        "participant_id": participant_id,
        "participants": roster.json()["data"]["participants"],
    }


# ---- 谁碰得了这道题 ----------------------------------------------------------


def test_a_task_without_an_external_number_reads_as_an_empty_block(
    api_client: TestClient, user_client: UserCreator
):
    """「这道题还没接外站」是一个答案，不是错误 —— 和「还没人报名」一样。"""
    board = _board_with_a_student(api_client, user_client, name="还没接外站的题")

    r = api_client.get(
        f"/tasks/{board['task_id']}/external-problem",
        headers=_auth(board["creator_token"]),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"] == {
        "problemNo": "",
        "source": "yoj",
        "lastSyncedAt": None,
        "status": "",
        "error": "",
        "results": [],
    }


def test_a_member_of_the_board_who_does_not_teach_it_is_refused_with_a_key(
    api_client: TestClient, user_client: UserCreator
):
    """板里的普通成员不是这道题的老师 —— 四扇门一个都不开。"""
    board = _board_with_a_student(api_client, user_client, name="只有老师能动")
    where = f"/tasks/{board['task_id']}/external-problem"
    headers = _auth(board["other_token"])

    calls = [
        api_client.get(where, headers=headers),
        api_client.put(where, json={"problemNo": "1234"}, headers=headers),
        api_client.delete(where, headers=headers),
        api_client.post(f"{where}/sync", headers=headers),
    ]

    for r in calls:
        assert r.status_code == 403, r.text
        assert r.json()["error"]["i18n"] == {"key": "taskOwnerOnly", "params": {}}


def test_a_board_admin_can_set_the_number_and_the_creator_can_read_it(
    api_client: TestClient, user_client: UserCreator
):
    board = _board_with_a_student(api_client, user_client, name="管理员也能挂")
    where = f"/tasks/{board['task_id']}/external-problem"

    saved = api_client.put(
        where, json={"problemNo": "1234"}, headers=_auth(board["teacher_token"])
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["problemNo"] == "1234"

    read = api_client.get(where, headers=_auth(board["creator_token"]))
    assert read.status_code == 200
    assert read.json()["data"]["problemNo"] == "1234"


def test_a_number_that_is_not_digits_is_refused_with_a_key(
    api_client: TestClient, user_client: UserCreator
):
    board = _board_with_a_student(api_client, user_client, name="题号只收数字")

    r = api_client.put(
        f"/tasks/{board['task_id']}/external-problem",
        json={"problemNo": "abc"},
        headers=_auth(board["creator_token"]),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["i18n"] == {
        "key": "externalProblemNumberInvalid",
        "params": {},
    }


def test_a_task_that_does_not_exist_is_not_found(
    api_client: TestClient, user_client: UserCreator
):
    board = _board_with_a_student(api_client, user_client, name="不存在的题")

    r = api_client.get(
        "/tasks/99999999/external-problem", headers=_auth(board["creator_token"])
    )

    assert r.status_code == 404


def test_syncing_before_a_number_is_set_says_so(
    api_client: TestClient, user_client: UserCreator
):
    board = _board_with_a_student(api_client, user_client, name="先挂题号再同步")

    r = api_client.post(
        f"/tasks/{board['task_id']}/external-problem/sync",
        headers=_auth(board["creator_token"]),
    )

    assert r.status_code == 404, r.text
    assert r.json()["error"]["i18n"] == {"key": "externalProblemNotSet", "params": {}}


def test_clearing_takes_the_number_away(
    api_client: TestClient, user_client: UserCreator
):
    board = _board_with_a_student(api_client, user_client, name="撤掉挂载")
    where = f"/tasks/{board['task_id']}/external-problem"
    headers = _auth(board["creator_token"])

    assert (
        api_client.put(where, json={"problemNo": "1234"}, headers=headers).status_code
        == 200
    )
    cleared = api_client.delete(where, headers=headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"] == {"deleted": True}

    assert api_client.get(where, headers=headers).json()["data"]["problemNo"] == ""


# ---- 同步接得上名单 ----------------------------------------------------------


def test_a_sync_answers_with_the_ids_the_roster_hands_out(
    api_client: TestClient, user_client: UserCreator, monkeypatch
):
    """前端要拿这一列贴回名单上，所以 ``participantId`` 必须就是名单那一行的 ``id``。"""
    board = _board_with_a_student(api_client, user_client, name="同步一次")
    where = f"/tasks/{board['task_id']}/external-problem"
    headers = _auth(board["creator_token"])

    # 账号是平台级的一份，走管理员那条路由配一次（那一侧自己的用例在下面）。
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    saved_account = api_client.put(
        "/admin/integrations/external-problem",
        json={"username": "20210129", "password": "pw"},
        headers=session_auth_headers(ADMIN),
    )
    assert saved_account.status_code == 200, saved_account.text

    assert (
        api_client.put(where, json={"problemNo": "1234"}, headers=headers).status_code
        == 200
    )
    solved = {board["student"].username: "1234"}
    monkeypatch.setattr(service_module, "_yoj_client", lambda u, p: _FakeYoj(solved))

    r = api_client.post(f"{where}/sync", headers=headers)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "ok"
    assert data["lastSyncedAt"] is not None
    row = data["results"][0]
    assert row["participantId"] == board["participant_id"]
    assert row["participantId"] in [p["id"] for p in board["participants"]]
    assert row["studentNo"] == board["student"].username
    assert row["solved"] is True
    assert row["acceptedAt"] is not None


def test_a_sync_that_could_not_run_still_answers_200(
    api_client: TestClient, user_client: UserCreator
):
    """失败的是「外站这一趟没去成」，不是「你这个请求不对」—— 没配账号时也一样。"""
    board = _board_with_a_student(api_client, user_client, name="没账号也能按")
    where = f"/tasks/{board['task_id']}/external-problem"
    headers = _auth(board["creator_token"])
    assert (
        api_client.put(where, json={"problemNo": "1234"}, headers=headers).status_code
        == 200
    )

    r = api_client.post(f"{where}/sync", headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "failed"
    assert r.json()["data"]["error"] == "no_account"


# ---- 平台管理员那一侧 --------------------------------------------------------


ADMIN = "external-problem-admin"


def test_a_non_admin_cannot_read_or_write_the_account(client, monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])

    read = client.get(
        "/admin/integrations/external-problem",
        headers=session_auth_headers("someone-else"),
    )
    saved = client.put(
        "/admin/integrations/external-problem",
        json={"username": "20210129", "password": "pw"},
        headers=session_auth_headers("someone-else"),
    )

    for r in (read, saved):
        assert r.status_code == 403, r.text
        assert r.json()["error"]["i18n"] == {
            "key": "platformAdminRequired",
            "params": {},
        }


def test_the_account_page_never_shows_a_password(client, monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    headers = session_auth_headers(ADMIN)

    empty = client.get("/admin/integrations/external-problem", headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["data"] == {
        "configured": False,
        "source": "yoj",
        "username": "",
        "updated_by": "",
        "updated_at": None,
    }

    saved = client.put(
        "/admin/integrations/external-problem",
        json={"username": "20210129", "password": "pw-secret"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    assert data["configured"] is True
    assert data["username"] == "20210129"
    assert data["updated_by"] == ADMIN
    assert "pw-secret" not in saved.text


def test_saving_the_account_again_without_a_password_keeps_it(client, monkeypatch):
    """页面看不到密码，所以留空只能是「不改」—— 改个账号名不该逼管理员找回口令。"""
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    headers = session_auth_headers(ADMIN)
    client.put(
        "/admin/integrations/external-problem",
        json={"username": "20210129", "password": "pw-secret"},
        headers=headers,
    )

    again = client.put(
        "/admin/integrations/external-problem",
        json={"username": "teach2", "password": ""},
        headers=headers,
    )

    assert again.status_code == 200, again.text
    assert again.json()["data"]["username"] == "teach2"

    from app.domain.external_problem.service import (
        ExternalProblemService,
        unseal_account,
    )

    async def _read_secret() -> str:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            row = await ExternalProblemService(session).account()
            return unseal_account(row)["password"]

    assert client.portal.call(_read_secret) == "pw-secret"


# ---- 文档声明的形状和真发出去的对得上 ----------------------------------------


def test_the_declared_response_shape_is_the_shape_the_views_build():
    """``typed_response`` 只写文档、不校验（``app/api/response.py``），所以两边一不
    一致没有别的东西盯着。这条就是那个盯着的测试。"""
    empty = binding_view(None, [])
    assert set(empty) == set(TaskExternalProblemOut.model_fields)

    filled = binding_view(_Binding(), [_Result()])
    assert set(filled) == set(TaskExternalProblemOut.model_fields)
    assert set(filled["results"][0]) == set(ExternalProblemResultOut.model_fields)

    assert set(account_view(None)) == set(ExternalProblemAccountOut.model_fields)
    assert set(result_view(_Result())) == set(ExternalProblemResultOut.model_fields)


class _Binding:
    problem_no = "1234"
    source = "yoj"
    last_synced_at = datetime.fromtimestamp(AT, UTC)
    last_status = "ok"
    last_error = ""


class _Result:
    member_id = 7
    student_no = "20210129"
    solved = True
    accepted_at = datetime.fromtimestamp(AT, UTC)
