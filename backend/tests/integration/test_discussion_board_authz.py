"""越权回归：讨论板的每一条路由都只认登录、不认它挂着的那块板。

``backend/app/api/routes/discussions.py`` 的 8 个 handler 从前只挂了
``Depends(require_auth_user)``：``modelType`` / ``modelId`` 是客户端给的字符串和
整数，直接当作 ``discussion`` 表上的两列去比较（``app/domain/discussion/
repositories.py`` 的 ``find_all``），被挂的那个对象本身**从不加载**。于是
「这块板存不存在、这个人看不看得见它」从来没有被问过。

实测过的那条路径（修复前，两个用户，B 与 A 的题目板毫无关系）：

* ``GET /spaces/{id}`` → 404（空间对 B 不存在）
* 同一秒 ``GET /discussions?modelType=SPACE&modelId={id}`` → **200，且含 A 的原文**
* ``GET /discussions/{id}`` → 200；``GET /discussions/{id}/sub-discussions`` → 200
* ``POST /discussions`` → **201**（在看不见的板上发帖成功）
* ``POST /discussions/{id}/reactions/1`` → 200

这份用例把那条路径钉死，并且反过来守住正常路径：板的成员读写照旧。判据是各域自己
已经问了很多遍的那一条 —— SPACE 走 ``_ensure_space_visible``，所以非成员得到的答案
与 ``/spaces/{spaceId}/...`` 逐字相同：404，而不是 403（一个你不在的题目板不该被确认
存在）。
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, create_approved_space, unique_int


def _registered(user_client: UserCreator, api_client: TestClient):
    """一个真的注册过、也登录了的用户，带着它的 token。"""
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _bearer(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


class TestDiscussionBoardBelongsToItsParent:
    @pytest.fixture
    def board(self, user_client: UserCreator, api_client: TestClient) -> dict:
        """A 的题目板（已过审），板上一条 A 的帖子；B 与这块板无关。"""
        alice = _registered(user_client, api_client)
        bob = _registered(user_client, api_client)

        suffix = unique_int(10000000, 99999999)
        space_resp = create_approved_space(
            api_client,
            json={
                "name": f"Authz Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers=_bearer(alice),
        )
        assert space_resp.status_code == 201, space_resp.text
        space_id = space_resp.json()["data"]["space"]["id"]

        post = api_client.post(
            "/discussions",
            json={
                "modelType": "SPACE",
                "modelId": space_id,
                "content": "A 的原文，B 不该看得到",
            },
            headers=_bearer(alice),
        )
        assert post.status_code == 201, post.text
        discussion_id = post.json()["data"]["discussion"]["id"]

        reply = api_client.post(
            "/discussions",
            json={
                "modelType": "SPACE",
                "modelId": space_id,
                "content": "A 的回复",
                "parentId": discussion_id,
            },
            headers=_bearer(alice),
        )
        assert reply.status_code == 201, reply.text

        return {
            "alice": alice,
            "bob": bob,
            "space_id": space_id,
            "discussion_id": discussion_id,
            "reply_id": reply.json()["data"]["discussion"]["id"],
        }

    # --- 非成员：整块板都不该有答案 -------------------------------------

    def test_a_stranger_cannot_even_see_the_board_it_hangs_on(
        self, board: dict, api_client: TestClient
    ):
        """先证明「空间对 B 不存在」，后面的 404 才有意义 —— 同一个答案。"""
        resp = api_client.get(
            f"/spaces/{board['space_id']}", headers=_bearer(board["bob"])
        )
        assert resp.status_code == 404, resp.text

    def test_a_stranger_cannot_list_the_boards_discussions(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.get(
            "/discussions",
            params={"modelType": "SPACE", "modelId": board["space_id"]},
            headers=_bearer(board["bob"]),
        )
        assert resp.status_code == 404, (
            f"非成员读到了别人的讨论板：{resp.status_code} {resp.text}"
        )
        assert "A 的原文" not in resp.text

    def test_a_stranger_cannot_post_into_the_board(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.post(
            "/discussions",
            json={
                "modelType": "SPACE",
                "modelId": board["space_id"],
                "content": "B 在看不见的板上发帖",
            },
            headers=_bearer(board["bob"]),
        )
        assert resp.status_code == 404, (
            f"非成员在别人的板上发帖成功：{resp.status_code} {resp.text}"
        )

    def test_a_stranger_cannot_read_a_single_discussion(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.get(
            f"/discussions/{board['discussion_id']}", headers=_bearer(board["bob"])
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"
        assert "A 的原文" not in resp.text

    def test_a_stranger_cannot_read_the_replies(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.get(
            f"/discussions/{board['discussion_id']}/sub-discussions",
            headers=_bearer(board["bob"]),
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"
        assert "A 的回复" not in resp.text

    def test_a_stranger_cannot_react(self, board: dict, api_client: TestClient):
        resp = api_client.post(
            f"/discussions/{board['discussion_id']}/reactions/1",
            headers=_bearer(board["bob"]),
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"

    def test_a_stranger_cannot_remove_a_reaction(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.delete(
            f"/discussions/{board['discussion_id']}/reactions/1",
            headers=_bearer(board["bob"]),
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"

    def test_a_stranger_cannot_edit_someone_elses_post(
        self, board: dict, api_client: TestClient
    ):
        """改删本来就有「只有作者能改」，现在连「你看不见这条」也一并答对了。"""
        resp = api_client.patch(
            f"/discussions/{board['discussion_id']}",
            json={"content": "B 改的"},
            headers=_bearer(board["bob"]),
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"

    def test_a_stranger_cannot_delete_someone_elses_post(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.delete(
            f"/discussions/{board['discussion_id']}", headers=_bearer(board["bob"])
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"

    def test_a_stranger_cannot_forge_a_reply_into_someone_elses_thread(
        self, board: dict, api_client: TestClient
    ):
        """``parentId`` 也是客户端给的：不能把一条回复折进别人的楼里。

        B 拿 A 的帖子 id 当父对象、把行挂在自己的板上 —— 从前会被接受，于是 A 的
        楼里凭空多出一段 B 写的内容。父对象不属于这块板，一律「这条讨论不存在」。
        """
        bob_space = create_approved_space(
            api_client,
            json={
                "name": f"Bob Space ({unique_int(10000000, 99999999)})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers=_bearer(board["bob"]),
        )
        assert bob_space.status_code == 201, bob_space.text
        bob_space_id = bob_space.json()["data"]["space"]["id"]

        resp = api_client.post(
            "/discussions",
            json={
                "modelType": "SPACE",
                "modelId": bob_space_id,
                "content": "B 折进 A 楼里的回复",
                "parentId": board["discussion_id"],
            },
            headers=_bearer(board["bob"]),
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"

    # --- 成员：自己的板照旧 --------------------------------------------

    def test_a_member_still_reads_and_writes_the_board(
        self, board: dict, api_client: TestClient
    ):
        alice = board["alice"]

        listed = api_client.get(
            "/discussions",
            params={"modelType": "SPACE", "modelId": board["space_id"]},
            headers=_bearer(alice),
        )
        assert listed.status_code == 200, listed.text
        assert "A 的原文" in listed.text

        single = api_client.get(
            f"/discussions/{board['discussion_id']}", headers=_bearer(alice)
        )
        assert single.status_code == 200, single.text

        subs = api_client.get(
            f"/discussions/{board['discussion_id']}/sub-discussions",
            headers=_bearer(alice),
        )
        assert subs.status_code == 200, subs.text
        assert "A 的回复" in subs.text

        posted = api_client.post(
            "/discussions",
            json={
                "modelType": "SPACE",
                "modelId": board["space_id"],
                "content": "A 又发了一条",
            },
            headers=_bearer(alice),
        )
        assert posted.status_code == 201, posted.text

        reacted = api_client.post(
            f"/discussions/{board['discussion_id']}/reactions/1",
            headers=_bearer(alice),
        )
        assert reacted.status_code == 200, reacted.text

        edited = api_client.patch(
            f"/discussions/{board['discussion_id']}",
            json={"content": "A 改了自己的帖子"},
            headers=_bearer(alice),
        )
        assert edited.status_code == 200, edited.text

    def test_a_member_can_reply_inside_the_same_board(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.post(
            "/discussions",
            json={
                "modelType": "SPACE",
                "modelId": board["space_id"],
                "content": "A 回自己的楼",
                "parentId": board["discussion_id"],
            },
            headers=_bearer(board["alice"]),
        )
        assert resp.status_code == 201, resp.text

    # --- 没有父对象 / 不认识的父对象 -----------------------------------

    def test_list_without_a_board_is_refused(self, board: dict, api_client: TestClient):
        """不给 modelType / modelId 从前等于「不过滤」= 全平台讨论总汇。"""
        resp = api_client.get("/discussions", headers=_bearer(board["alice"]))
        assert resp.status_code == 400, f"{resp.status_code} {resp.text}"

        only_type = api_client.get(
            "/discussions",
            params={"modelType": "SPACE"},
            headers=_bearer(board["alice"]),
        )
        assert only_type.status_code == 400, only_type.text

    def test_an_unknown_model_type_is_refused(
        self, board: dict, api_client: TestClient
    ):
        listed = api_client.get(
            "/discussions",
            params={"modelType": "NOT_A_MODEL_TYPE", "modelId": 1},
            headers=_bearer(board["alice"]),
        )
        assert listed.status_code == 400, f"{listed.status_code} {listed.text}"

        posted = api_client.post(
            "/discussions",
            json={
                "modelType": "NOT_A_MODEL_TYPE",
                "modelId": 1,
                "content": "x",
            },
            headers=_bearer(board["alice"]),
        )
        assert posted.status_code == 400, f"{posted.status_code} {posted.text}"

    def test_a_missing_discussion_is_still_a_404(
        self, board: dict, api_client: TestClient
    ):
        resp = api_client.get("/discussions/999999999", headers=_bearer(board["alice"]))
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"


class TestTheOtherModelTypesAskTheirOwnDomain:
    """``model_type`` 不止 SPACE —— 每一种都走它自己域已有的那条判据。

    这里钉住的是「哪一句话」，而不是重抄一遍那些门的实现：改错的判据会在这里露出来。
    """

    @staticmethod
    def _user(user_client: UserCreator, api_client: TestClient):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        return user

    def test_project_is_never_addressable(
        self, user_client: UserCreator, api_client: TestClient
    ):
        """项目主键是 UUID，``Discussion.model_id`` 是整数 —— 没有哪个整数指得到项目，
        父对象加载不出来，所以一律 404（fail closed），对谁都是。"""
        someone = self._user(user_client, api_client)
        resp = api_client.get(
            "/discussions",
            params={"modelType": "PROJECT", "modelId": 1},
            headers=_bearer(someone),
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text}"

    def test_task_boards_follow_the_task_visibility(
        self, user_client: UserCreator, api_client: TestClient
    ):
        """不存在的题 → 404；题存在时，能不能看这块板由 ``can_view_task`` 说话。"""
        someone = self._user(user_client, api_client)
        missing = api_client.get(
            "/discussions",
            params={"modelType": "TASK", "modelId": 999999999},
            headers=_bearer(someone),
        )
        assert missing.status_code == 404, f"{missing.status_code} {missing.text}"

        created = api_client.post(
            "/discussions",
            json={"modelType": "TASK", "modelId": 999999999, "content": "x"},
            headers=_bearer(someone),
        )
        assert created.status_code == 404, f"{created.status_code} {created.text}"

    def test_team_boards_follow_team_visibility(
        self, user_client: UserCreator, api_client: TestClient
    ):
        owner = self._user(user_client, api_client)
        stranger = self._user(user_client, api_client)

        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Authz Team ({unique_int(10000000, 99999999)})",
                "intro": "Test",
                "description": "A lengthy text. " * 100,
                "avatarId": 1,
            },
            headers=_bearer(owner),
        )
        assert team_resp.status_code == 201, team_resp.text
        team_id = team_resp.json()["data"]["team"]["id"]

        posted = api_client.post(
            "/discussions",
            json={"modelType": "TEAM", "modelId": team_id, "content": "队内帖子"},
            headers=_bearer(owner),
        )
        assert posted.status_code == 201, posted.text

        member_read = api_client.get(
            "/discussions",
            params={"modelType": "TEAM", "modelId": team_id},
            headers=_bearer(owner),
        )
        assert member_read.status_code == 200, member_read.text
        assert "队内帖子" in member_read.text

        # 与 GET /teams/{teamId} 同一句话，公开团队两处都 200。
        public_team = api_client.get(f"/teams/{team_id}", headers=_bearer(stranger))
        public_read = api_client.get(
            "/discussions",
            params={"modelType": "TEAM", "modelId": team_id},
            headers=_bearer(stranger),
        )
        assert public_read.status_code == public_team.status_code == 200, (
            f"team={public_team.status_code} board={public_read.status_code}"
        )

        # 收成隐身团队之后，两处一起答 404。
        patched = api_client.patch(
            f"/teams/{team_id}",
            json={"visibility": "stealth"},
            headers=_bearer(owner),
        )
        assert patched.status_code == 200, patched.text

        hidden_team = api_client.get(f"/teams/{team_id}", headers=_bearer(stranger))
        assert hidden_team.status_code == 404, hidden_team.text

        stranger_read = api_client.get(
            "/discussions",
            params={"modelType": "TEAM", "modelId": team_id},
            headers=_bearer(stranger),
        )
        assert stranger_read.status_code == 404, (
            f"{stranger_read.status_code} {stranger_read.text}"
        )
        assert "队内帖子" not in stranger_read.text

        # 成员照旧看得见。
        still_visible = api_client.get(
            f"/discussions/{posted.json()['data']['discussion']['id']}",
            headers=_bearer(owner),
        )
        assert still_visible.status_code == 200, still_visible.text

    def test_knowledge_boards_follow_the_knowledge_team_gate(
        self, user_client: UserCreator, api_client: TestClient
    ):
        owner = self._user(user_client, api_client)
        stranger = self._user(user_client, api_client)

        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Knowledge Authz Team ({unique_int(10000000, 99999999)})",
                "intro": "Test",
                "description": "A lengthy text. " * 100,
                "avatarId": 1,
            },
            headers=_bearer(owner),
        )
        assert team_resp.status_code == 201, team_resp.text
        team_id = team_resp.json()["data"]["team"]["id"]

        knowledge_resp = api_client.post(
            "/knowledge",
            json={
                "name": f"Authz Knowledge {unique_int(100000, 999999)}",
                "description": "Test",
                "type": "TEXT",
                "content": {"text": "Test content"},
                "teamId": team_id,
                "labels": [],
            },
            headers=_bearer(owner),
        )
        assert knowledge_resp.status_code == 201, knowledge_resp.text
        knowledge_id = knowledge_resp.json()["data"]["knowledge"]["id"]

        posted = api_client.post(
            "/discussions",
            json={
                "modelType": "KNOWLEDGE",
                "modelId": knowledge_id,
                "content": "条目下的讨论",
            },
            headers=_bearer(owner),
        )
        assert posted.status_code == 201, posted.text

        member_read = api_client.get(
            "/discussions",
            params={"modelType": "KNOWLEDGE", "modelId": knowledge_id},
            headers=_bearer(owner),
        )
        assert member_read.status_code == 200, member_read.text

        # 知识域自己的门是「团队成员」，不是成员答 403（与 /knowledge/{id} 一致）。
        stranger_read = api_client.get(
            "/discussions",
            params={"modelType": "KNOWLEDGE", "modelId": knowledge_id},
            headers=_bearer(stranger),
        )
        assert stranger_read.status_code == 403, (
            f"{stranger_read.status_code} {stranger_read.text}"
        )

    def test_question_boards_are_as_public_as_the_question(
        self, user_client: UserCreator, api_client: TestClient
    ):
        """学习问答对每个登录用户公开（``GET /questions/{id}`` 本来就不逐人判断），
        所以它下面的讨论也一样；不存在的题则 404。"""
        author = self._user(user_client, api_client)
        reader = self._user(user_client, api_client)

        question_resp = api_client.post(
            "/questions",
            json={
                "title": f"Authz Question {unique_int(100000, 999999)}",
                "content": "body",
                "type": 0,
                "topics": [],
                "groupId": None,
                "bounty": 0,
            },
            headers=_bearer(author),
        )
        assert question_resp.status_code == 201, question_resp.text
        question_id = question_resp.json()["data"]["id"]

        posted = api_client.post(
            "/discussions",
            json={
                "modelType": "QUESTION",
                "modelId": question_id,
                "content": "问题下的讨论",
            },
            headers=_bearer(author),
        )
        assert posted.status_code == 201, posted.text

        readable = api_client.get(
            "/discussions",
            params={"modelType": "QUESTION", "modelId": question_id},
            headers=_bearer(reader),
        )
        assert readable.status_code == 200, readable.text
        assert "问题下的讨论" in readable.text

        missing = api_client.get(
            "/discussions",
            params={"modelType": "QUESTION", "modelId": 999999999},
            headers=_bearer(reader),
        )
        assert missing.status_code == 404, f"{missing.status_code} {missing.text}"
