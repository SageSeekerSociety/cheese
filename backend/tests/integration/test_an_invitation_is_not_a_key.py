"""导师进项目要两份东西：一份邀请，一次接受——邀请本身不是钥匙。

#945 的教师看板要读一个项目的对话与 AI 摘要，而「导师」只是项目成员里的一种
角色（``ProjectRole.mentor``）。角色不改变门槛：进了名册就看得见这个项目的**全部
话题**，所以「谁能进名册」这件事决定的是谁能读到别人的工作内容。平台为此把加人拆
成两步——``InvitationService`` 记下邀请，``respond(accept=True)`` 才是那一步真
正把人放上名册的写。这一份钉的就是这两步之间的那条缝：

1. 被邀请、还没答复的人什么也读不到。邀请是一条待办，不是凭据；如果读得到，
   「要对方点头」就只是界面上的礼貌，而不是一道门。
2. 答复之后，mentor 和 member 一样读得到——教师看板要的正是这条，收窄不能把
   它一起收掉。
3. 邀请被撤回之后，答复不了，也读不到。撤回是邀请方反悔，而反悔必须发生在
   **开门之前**。
4. 已经在名册上的导师被移出，门同样立刻关——撤权不因为角色是导师而慢一拍。

每条断言都是浏览器会收到的状态码，不看实现。
"""

from tests.conftest import seed_user
from tests.integration.test_team_member_enters_team_project import _bearer
from tests.integration.test_who_may_read_a_project import DOORS, _project

OWNER = "alice"
MENTOR = "mentor-1"


def _headers(client, handle: str) -> dict[str, str]:
    return _bearer(seed_user(client, handle))


def _invite_mentor(client, project_id: str) -> str:
    r = client.post(
        f"/projects/{project_id}/invitations",
        json={"user_handle": MENTOR, "role": "mentor"},
        headers=_headers(client, OWNER),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["role"] == "mentor"
    return r.json()["data"]["id"]


def _answer(client, invitation_id: str, *, accept: bool):
    return client.post(
        f"/invitations/{invitation_id}/respond",
        json={"accept": accept},
        headers=_headers(client, MENTOR),
    )


def _codes(client, project_id: str) -> list[int]:
    """导师逐个门收到的状态码。同一支请求、同样的参数，逐次真发。"""
    headers = _headers(client, MENTOR)
    return [
        client.get(path.format(pid=project_id), headers=headers).status_code
        for path in DOORS.values()
    ]


def test_an_unanswered_invitation_is_not_a_key(client):
    """还没答复的人读不到——包括 AI 摘要那条聚合。"""
    pid, _ = _project(client)
    _invite_mentor(client, pid)

    assert set(_codes(client, pid)) == {403}


def test_a_mentor_who_answered_reads_every_door(client):
    """答复之后，mentor 和 member 走同一扇门 —— 教师看板要的就是这条。"""
    pid, _ = _project(client)
    invitation = _invite_mentor(client, pid)

    assert _answer(client, invitation, accept=True).status_code == 200
    assert set(_codes(client, pid)) == {200}


def test_a_revoked_invitation_is_not_a_key_either(client):
    """撤回发生在开门之前 —— 撤回之后答复不了，也读不到。"""
    pid, _ = _project(client)
    invitation = _invite_mentor(client, pid)

    revoked = client.delete(
        f"/invitations/{invitation}", headers=_headers(client, OWNER)
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == "revoked"

    assert _answer(client, invitation, accept=True).status_code == 422
    assert set(_codes(client, pid)) == {403}


def test_a_removed_mentor_loses_every_door_at_once(client):
    """撤权不因为角色是导师而慢一拍：移出名册，下一次请求就得被拒。"""
    pid, _ = _project(client)
    invitation = _invite_mentor(client, pid)
    assert _answer(client, invitation, accept=True).status_code == 200
    assert set(_codes(client, pid)) == {200}

    removed = client.delete(
        f"/projects/{pid}/members/{MENTOR}", headers=_headers(client, OWNER)
    )
    assert removed.status_code == 200
    assert set(_codes(client, pid)) == {403}
