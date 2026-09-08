"""邀请：加人这件事要两个人同意。

为什么不是一步到位——进了项目就看得见这个项目的**全部话题**，那是别人的工作内容，
不该由邀请方单方面决定谁能看。所以这一份钉的全是「谁做的决定」：没答复之前不算成
员、只有本人能答复、答复过的邀请不能再答一次，以及那条待办不会在答复之后还挂在别
人的收件箱里等一个已经没有答案的问题。
"""

OWNER = "owner-1"


def _project(client, name: str = "Demo") -> str:
    r = client.post("/projects", json={"name": name, "owner_handle": OWNER})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _invite(client, bearer, project_id: str, handle: str, role: str = "member"):
    return client.post(
        f"/projects/{project_id}/invitations",
        json={"user_handle": handle, "role": role},
        headers=bearer(OWNER),
    )


def _handles(client, project_id: str) -> set[str]:
    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    return {m["user_handle"] for m in rows}


def test_an_invitation_does_not_put_anyone_on_the_roster(client, bearer):
    project_id = _project(client)
    r = _invite(client, bearer, project_id, "alice", "lead")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "pending"

    # 名册上没有她——这就是这个功能的全部意义。
    assert "alice" not in _handles(client, project_id)
    pending = client.get(f"/projects/{project_id}/invitations").json()["data"]["data"]
    assert [(i["invitee_handle"], i["role"]) for i in pending] == [("alice", "lead")]


def test_accepting_is_what_joins_the_project(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice", "lead").json()["data"]

    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert "alice" in _handles(client, project_id)
    # 邀请上写的角色就是她进来时的角色。
    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    assert next(m for m in rows if m["user_handle"] == "alice")["role"] == "lead"
    # 答复完就不再挂在待答复里。
    assert (
        client.get(f"/projects/{project_id}/invitations").json()["data"]["data"] == []
    )


def test_declining_leaves_the_roster_alone(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": False},
        headers=bearer("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "declined"
    assert "alice" not in _handles(client, project_id)


def test_only_the_invitee_can_answer(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    # 连发邀请的人自己都不行——否则「要对方同意」就是一句空话。
    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer(OWNER),
    )
    assert r.status_code == 403
    assert "alice" not in _handles(client, project_id)

    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("mallory"),
    )
    assert r.status_code == 403


def test_an_answered_invitation_cannot_be_answered_again(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]
    client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": False},
        headers=bearer("alice"),
    )
    # 界面上那颗按钮点两下不该得到一句莫名其妙的 404。
    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    assert r.status_code == 422
    assert "答复过" in r.json()["message"]
    assert "alice" not in _handles(client, project_id)


def test_declining_leaves_the_door_open_for_a_second_invitation(client, bearer):
    """拒绝一次不等于永远进不来——唯一约束只管「同时只能有一张待答复的」。"""
    project_id = _project(client)
    first = _invite(client, bearer, project_id, "alice").json()["data"]
    client.post(
        f"/invitations/{first['id']}/respond",
        json={"accept": False},
        headers=bearer("alice"),
    )
    assert _invite(client, bearer, project_id, "alice").status_code == 200


def test_the_same_person_is_not_invited_twice_over(client, bearer):
    project_id = _project(client)
    assert _invite(client, bearer, project_id, "alice").status_code == 200
    r = _invite(client, bearer, project_id, "alice")
    assert r.status_code == 422
    assert "等他答复" in r.json()["message"]


def test_someone_already_in_the_project_is_not_invited(client, bearer):
    project_id = _project(client)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "alice"},
        headers=bearer(OWNER),
    )
    r = _invite(client, bearer, project_id, "alice")
    assert r.status_code == 422
    assert "已经在项目里" in r.json()["message"]


def test_only_a_manager_may_invite(client, bearer):
    project_id = _project(client)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "bob", "role": "member"},
        headers=bearer(OWNER),
    )
    r = client.post(
        f"/projects/{project_id}/invitations",
        json={"user_handle": "alice"},
        headers=bearer("bob"),
    )
    assert r.status_code == 403


def test_an_invitation_says_which_project_it_is_for(client, bearer):
    """被邀请的人还不在这个项目里，任何项目作用域的接口他都够不着——项目名不由后端
    带出来，界面上就只能写「有人邀请你加入一个项目」，那是一句没法据以决定的话。"""
    project_id = _project(client, "推荐算法原型")
    _invite(client, bearer, project_id, "alice")
    mine = client.get("/me/invitations", headers=bearer("alice")).json()["data"]["data"]
    assert [i["project_name"] for i in mine] == ["推荐算法原型"]


def test_my_invitations_are_addressed_to_me_only(client, bearer):
    a = _project(client, "A")
    b = _project(client, "B")
    _invite(client, bearer, a, "alice")
    _invite(client, bearer, b, "bob")

    mine = client.get("/me/invitations", headers=bearer("alice")).json()["data"]["data"]
    assert [(i["project_id"], i["invitee_handle"]) for i in mine] == [(a, "alice")]
    # 被邀请的人还不在那个项目里，所以这条接口不能是项目作用域的——否则他根本够不着。
    assert (
        client.get("/me/invitations", headers=bearer("carol")).json()["data"]["data"]
        == []
    )


def test_an_answered_invitation_stops_waiting_in_the_inbox(client, bearer):
    """邀请是一条**待办**：答复之前不消失，答复之后必须消失。

    不结掉的话，一张已经答复的邀请会永远挂在收件箱里等他回答一个已经没有答案的
    问题。
    """
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    inbox = client.get(
        f"/projects/{project_id}/inbox?target_handle=alice", headers=bearer("alice")
    ).json()["data"]["data"]
    waiting = [a for a in inbox if a["resolved_at"] is None]
    assert waiting, "收到邀请应该在收件箱里留一条待办"
    assert any("邀请你加入项目" in a["title"] for a in waiting)

    client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    inbox = client.get(
        f"/projects/{project_id}/inbox?target_handle=alice", headers=bearer("alice")
    ).json()["data"]["data"]
    assert [a for a in inbox if a["resolved_at"] is None] == []


def test_revoking_takes_the_invitation_back(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    r = client.delete(f"/invitations/{invitation['id']}", headers=bearer(OWNER))
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "revoked"
    assert (
        client.get(f"/projects/{project_id}/invitations").json()["data"]["data"] == []
    )
    # 撤回之后对方点接受也没用。
    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    assert r.status_code == 422
    assert "alice" not in _handles(client, project_id)


def test_an_outsider_cannot_revoke(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]
    r = client.delete(f"/invitations/{invitation['id']}", headers=bearer("mallory"))
    assert r.status_code == 403
