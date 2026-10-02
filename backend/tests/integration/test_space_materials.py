"""题目板的共用资料库 (#944)：上传、两档可见性、下载，以及那道通用读闩。

四条判据各自钉一遍：

1. **传 / 删 / 改档**只对板的管理员开 —— 成员传一份上去是 403。
2. **清单**对成员开，但「仅管理员」那一档的行**不出现**（不是出现了点不开）。
3. **字节**：成员下得到「所有成员」档、下不到「仅管理员」档；管理员两档都下得到。
   看不见板子的人拿 404（不是 403）：一块你不在的板子不该被确认存在。
4. **通用读门的闩**：`GET /materials/{id}` 从前只要求登录、返回体里带着公开的
   `url`；素材进了「仅管理员」档之后，非管理员从那道门拿 403，改回「所有成员」
   档又拿得到 —— 这道闩只挡真的进了那一档的素材。

字节一律走 `GET /spaces/{spaceId}/materials/{materialId}/download`；清单里从没有
`url` 字段，这条也断言一遍：档位若靠前端不看某个字段来成立，它就不成立。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)

PDF_BYTES = b"%PDF-1.4 board material"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, client: TestClient) -> CreatedUser:
    user = user_client.create_user()
    user.token = user_client.login(client, user.username, user.password)
    return user


class _Board:
    """一块过审的板：一个所有者（管理员）、一个成员、一个局外人。"""

    def __init__(self, client: TestClient, user_client: UserCreator) -> None:
        self.client = client
        self.owner = _login(user_client, client)
        self.member = _login(user_client, client)
        self.outsider = _login(user_client, client)

        response = create_approved_space(
            client,
            json={
                "name": f"Materials Board ({unique_int(10000000, 99999999)})",
                "intro": "",
                "description": "",
                "avatarId": 1,
                "enableRank": False,
                "taskTemplates": [],
            },
            headers=_auth(self.owner.token),
        )
        assert response.status_code == 201, response.text
        self.space_id = response.json()["data"]["space"]["id"]

        added = client.post(
            f"/spaces/{self.space_id}/members",
            json={"userId": self.member.user_id},
            headers=_auth(self.owner.token),
        )
        assert added.status_code == 201, added.text

    def upload(
        self, token: str, *, visibility: str | None = None, body: bytes = PDF_BYTES
    ):
        data = {"type": "file"}
        if visibility is not None:
            data["visibility"] = visibility
        return self.client.post(
            f"/spaces/{self.space_id}/materials",
            files={"file": ("handout.pdf", io.BytesIO(body), "application/pdf")},
            data=data,
            headers=_auth(token),
        )

    def list(self, token: str):
        return self.client.get(
            f"/spaces/{self.space_id}/materials", headers=_auth(token)
        )

    def download(self, token: str, material_id: int):
        return self.client.get(
            f"/spaces/{self.space_id}/materials/{material_id}/download",
            headers=_auth(token),
        )

    def set_visibility(self, token: str, material_id: int, visibility: str):
        return self.client.patch(
            f"/spaces/{self.space_id}/materials/{material_id}",
            json={"visibility": visibility},
            headers=_auth(token),
        )

    def remove(self, token: str, material_id: int):
        return self.client.delete(
            f"/spaces/{self.space_id}/materials/{material_id}",
            headers=_auth(token),
        )


@pytest.fixture
def board(api_client: TestClient, user_client: UserCreator) -> _Board:
    return _Board(api_client, user_client)


class TestSpaceMaterialLibrary:
    def test_manager_uploads_and_every_member_sees_it(self, board: _Board) -> None:
        created = board.upload(board.owner.token)
        assert created.status_code == 201, created.text
        item = created.json()["data"]["material"]
        assert item["name"] == "handout.pdf"
        assert item["visibility"] == "members"
        # 默认档就是「所有成员」；响应里没有可拿来绕过权限的公开地址。
        assert "url" not in item

        for token in (board.owner.token, board.member.token):
            listed = board.list(token)
            assert listed.status_code == 200, listed.text
            ids = [row["id"] for row in listed.json()["data"]["materials"]]
            assert item["id"] in ids

        # 谁能管由服务端说：同一份清单，管理员是 true、成员是 false ——
        # 界面靠这一格决定摆不摆上传/改档/移除那几个入口。
        assert board.list(board.owner.token).json()["data"]["canManage"] is True
        assert board.list(board.member.token).json()["data"]["canManage"] is False

    def test_member_cannot_upload(self, board: _Board) -> None:
        response = board.upload(board.member.token)
        assert response.status_code == 403

    def test_outsider_cannot_list_or_download(self, board: _Board) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]

        assert board.list(board.outsider.token).status_code == 404
        assert board.download(board.outsider.token, material_id).status_code == 404

    def test_bytes_round_trip_and_counter(self, board: _Board) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]

        got = board.download(board.member.token, material_id)
        assert got.status_code == 200, got.text
        assert got.content == PDF_BYTES
        assert "handout.pdf" in got.headers["content-disposition"]

        listed = board.list(board.owner.token).json()["data"]["materials"]
        row = next(r for r in listed if r["id"] == material_id)
        assert row["downloadCount"] == 1

    def test_admins_tier_is_hidden_from_members(self, board: _Board) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]
        switched = board.set_visibility(board.owner.token, material_id, "admins")
        assert switched.status_code == 200, switched.text
        assert switched.json()["data"]["material"]["visibility"] == "admins"

        # 成员：清单里根本没有这一行，字节也拿不到。
        visible = [
            row["id"]
            for row in board.list(board.member.token).json()["data"]["materials"]
        ]
        assert material_id not in visible
        assert board.download(board.member.token, material_id).status_code == 403

        # 管理员：两样都还在。
        manager_visible = [
            row["id"]
            for row in board.list(board.owner.token).json()["data"]["materials"]
        ]
        assert material_id in manager_visible
        assert board.download(board.owner.token, material_id).status_code == 200

    def test_member_cannot_change_visibility_or_delete(self, board: _Board) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]

        assert (
            board.set_visibility(board.member.token, material_id, "admins").status_code
            == 403
        )
        assert board.remove(board.member.token, material_id).status_code == 403
        # 成员那两次都没落到库上：档位与清单原样。
        assert board.download(board.member.token, material_id).status_code == 200

    def test_unknown_visibility_is_refused(self, board: _Board) -> None:
        response = board.upload(board.owner.token, visibility="teachers")
        assert response.status_code == 400

    def test_delete_takes_it_off_the_board(self, board: _Board) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]
        assert board.remove(board.owner.token, material_id).status_code == 204

        listed = [
            row["id"]
            for row in board.list(board.owner.token).json()["data"]["materials"]
        ]
        assert material_id not in listed
        assert board.download(board.owner.token, material_id).status_code == 404


class TestAdminsTierClosesTheGenericReadRoute:
    """`GET /materials/{id}` 只要求登录、返回体里带着公开 ``url`` —— 那道闩。"""

    def test_admins_tier_material_is_refused_to_non_managers(
        self, board: _Board
    ) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]

        # 「所有成员」档：谁都读得到，通用门照旧（今天的口径本来如此）。
        assert (
            board.client.get(
                f"/materials/{material_id}", headers=_auth(board.member.token)
            ).status_code
            == 200
        )

        board.set_visibility(board.owner.token, material_id, "admins")
        assert (
            board.client.get(
                f"/materials/{material_id}", headers=_auth(board.member.token)
            ).status_code
            == 403
        )
        assert (
            board.client.get(
                f"/materials/{material_id}", headers=_auth(board.outsider.token)
            ).status_code
            == 403
        )
        # 管理员照常。
        allowed = board.client.get(
            f"/materials/{material_id}", headers=_auth(board.owner.token)
        )
        assert allowed.status_code == 200

    def test_switching_back_to_members_reopens_it(self, board: _Board) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]
        board.set_visibility(board.owner.token, material_id, "admins")
        board.set_visibility(board.owner.token, material_id, "members")

        assert (
            board.client.get(
                f"/materials/{material_id}", headers=_auth(board.member.token)
            ).status_code
            == 200
        )

    def test_a_material_nobody_has_listed_is_untouched(
        self, board: _Board, user_client: UserCreator
    ) -> None:
        """没进过任何资料库的素材，通用读门一个字不变。"""
        uploader = _login(user_client, board.client)
        response = board.client.post(
            "/materials",
            files={"file": ("loose.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
            data={"type": "file"},
            headers=_auth(uploader.token),
        )
        assert response.status_code == 201, response.text
        material_id = response.json()["data"]["id"]

        assert (
            board.client.get(
                f"/materials/{material_id}", headers=_auth(board.outsider.token)
            ).status_code
            == 200
        )
