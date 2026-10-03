"""题目板的共用资料库 (#944)：上传、两档可见性、下载，以及那道通用读闩。

四条判据各自钉一遍：

1. **传 / 删 / 改档**只对板的管理员开 —— 成员传一份上去是 403。
2. **清单**对成员开，但「仅管理员」那一档的行**不出现**（不是出现了点不开）。
3. **字节**：成员下得到「所有成员」档、下不到「仅管理员」档；管理员两档都下得到。
   看不见板子的人拿 404（不是 403）：一块你不在的板子不该被确认存在。
4. **通用读门的闩**：`GET /materials/{id}` 从前只要求登录、返回体里带着公开的
   `url`；素材进了「仅管理员」档之后，非管理员从那道门拿 403，改回「所有成员」
   档又拿得到 —— 这道闩只挡真的进了那一档的素材。
5. **同一个 url 的另一条出口**：`GET /material-bundles/{id}` 也带着 `url`，也只
   要求登录，而素材 id 是小整数。所以非管理员自己开一个引用该 id 的空壳包、再读
   回来，看不见的素材在包里**根本不出现**（第 5 条那两道用例）。
6. **删素材**：素材进了资料库之后，上传者走通用的 `DELETE /materials/{id}` 仍然
   删得掉，关联跟着走（那条外键是 `ON DELETE CASCADE`）。

字节一律走 `GET /spaces/{spaceId}/materials/{materialId}/download`；清单里从没有
`url` 字段，这条也断言一遍：档位若靠前端不看某个字段来成立，它就不成立。
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

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

    # ── 引用面：三处教学配置各写一遍 ─────────────────────────────────────

    def teach_space(self, token: str, material_ids: list[int]):
        return self.client.patch(
            f"/spaces/{self.space_id}",
            json={"teaching": {"materialIds": material_ids}},
            headers=_auth(token),
        )

    def teach_category(self, token: str, material_ids: list[int]) -> int:
        """新建一个项目集并让它引用这几份。返回项目集 id。"""
        created = self.client.post(
            f"/spaces/{self.space_id}/categories",
            json={"name": f"集 {unique_int(1000, 9999)}"},
            headers=_auth(token),
        )
        assert created.status_code == 201, created.text
        category_id = created.json()["data"]["category"]["id"]
        patched = self.client.patch(
            f"/spaces/{self.space_id}/categories/{category_id}",
            json={"teaching": {"materialIds": material_ids}},
            headers=_auth(token),
        )
        assert patched.status_code == 200, patched.text
        return category_id

    def teach_task(self, token: str, material_ids: list[int]) -> int:
        """在默认项目集下发一道题，并让它引用这几份。返回题目 id。"""
        space = self.client.get(
            f"/spaces/{self.space_id}", headers=_auth(token)
        ).json()["data"]["space"]
        published = self.client.post(
            "/tasks",
            json={
                "name": f"题 {unique_int(1000, 9999)}",
                "intro": "题",
                "description": '{"type":"doc","content":[]}',
                "space": self.space_id,
                "categoryId": space.get("defaultCategoryId"),
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": int(datetime.now(UTC).timestamp() * 1000)
                + 7 * 86400 * 1000,
                "teaching": {"materialIds": material_ids},
            },
            headers=_auth(token),
        )
        assert published.status_code == 200, published.text
        return published.json()["data"]["task"]["id"]

    def drop_task(self, token: str, task_id: int):
        return self.client.delete(f"/tasks/{task_id}", headers=_auth(token))

    def drop_category(self, token: str, category_id: int):
        return self.client.delete(
            f"/spaces/{self.space_id}/categories/{category_id}", headers=_auth(token)
        )

    def counts(self, token: str) -> dict:
        """清单里每一行的 `usedByCount`；成员那一侧没有这个键，于是值是 None。"""
        listed = self.list(token)
        assert listed.status_code == 200, listed.text
        return {
            row["id"]: row.get("usedByCount")
            for row in listed.json()["data"]["materials"]
        }


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


def _bundle_with(client: TestClient, token: str, material_id: int) -> int:
    """开一个只引用这一份素材的包，返回包 id。这条路由只要求登录。"""
    created = client.post(
        "/material-bundles",
        json={
            "title": f"Bundle {unique_int(10000000, 99999999)}",
            "materialIds": [material_id],
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


class TestMaterialBundleDoesNotLeakHiddenMaterials:
    """素材包是 `url` 的第二条出口，和通用读门是同一句判据。"""

    def test_admins_tier_material_never_surfaces_in_a_bundle(
        self, board: _Board
    ) -> None:
        uploaded = board.upload(board.owner.token, visibility="admins")
        assert uploaded.status_code == 201, uploaded.text
        material_id = uploaded.json()["data"]["material"]["id"]

        # 局外人自己开一个引用它 —— 光有 id 就够（素材 id 是小整数，可枚举）。
        bundle_id = _bundle_with(board.client, board.outsider.token, material_id)

        seen = board.client.get(
            f"/material-bundles/{bundle_id}", headers=_auth(board.outsider.token)
        )
        assert seen.status_code == 200, seen.text
        assert seen.json()["data"]["materialBundle"]["materials"] == []
        # 不只是「没列出来」：地址本身一个字节都不在正文里。
        assert "/uploads/" not in seen.text
        assert "storageKey" not in seen.text

        # 板子的管理员照常读得到。
        owner_side = board.client.get(
            f"/material-bundles/{bundle_id}", headers=_auth(board.owner.token)
        )
        assert owner_side.status_code == 200, owner_side.text
        assert [
            material["id"]
            for material in owner_side.json()["data"]["materialBundle"]["materials"]
        ] == [material_id]

    def test_members_tier_material_still_travels_in_a_bundle(
        self, board: _Board
    ) -> None:
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]
        bundle_id = _bundle_with(board.client, board.member.token, material_id)

        seen = board.client.get(
            f"/material-bundles/{bundle_id}", headers=_auth(board.member.token)
        )
        assert seen.status_code == 200, seen.text
        assert [
            material["id"]
            for material in seen.json()["data"]["materialBundle"]["materials"]
        ] == [material_id]


class TestDeletingAMaterialOnABoard:
    def test_the_uploader_can_still_delete_it(self, board: _Board) -> None:
        """那条外键没有把既有的删除接口打坏：删得掉，关联跟着走。"""
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]

        gone = board.client.delete(
            f"/materials/{material_id}", headers=_auth(board.owner.token)
        )
        assert gone.status_code == 204, gone.text

        listed = board.list(board.owner.token)
        assert listed.status_code == 200, listed.text
        ids = [row["id"] for row in listed.json()["data"]["materials"]]
        assert material_id not in ids


class TestMaterialUsageCount:
    """清单里那一格「被几处引用」：只有能删的人看得到。

    数的是这块板上**写下来的**引用：空间默认、项目集、题目各算一处，一处里的
    同一份只算一次。项目那一层没有写入者（仓库里没有任何地方往
    `Project.settings["teaching"]` 写），所以它不在数里 —— 判据钉的就是这个范围。
    """

    def test_counts_come_from_the_three_written_layers(self, board: _Board) -> None:
        three = board.upload(board.owner.token).json()["data"]["material"]["id"]
        once = board.upload(board.owner.token).json()["data"]["material"]["id"]
        never = board.upload(board.owner.token).json()["data"]["material"]["id"]

        assert board.teach_space(board.owner.token, [three]).status_code == 200
        board.teach_category(board.owner.token, [three])
        board.teach_task(board.owner.token, [once, three])

        counts = board.counts(board.owner.token)
        assert counts[three] == 3
        assert counts[once] == 1
        assert counts[never] == 0

    def test_members_do_not_receive_the_column(self, board: _Board) -> None:
        """这一格是给「删之前先看看影响面」用的，成员删不了，也就不该拿到。

        判据是**键不在**，不是值为 0：值为 0 的那一行看着像「没人用，尽管删」。
        """
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]
        board.teach_space(board.owner.token, [material_id])

        assert board.counts(board.member.token)[material_id] is None

    def test_a_dangling_reference_does_not_disturb_the_rest(
        self, board: _Board
    ) -> None:
        """删课件不重写任何一层配置，所以空间那层会留一个悬空 id。

        那个 id 不该把这一格算错，也不该让接口报错 —— 与读侧「悬空引用跳过」
        同一口径：删掉的课件既不出现，也不影响别家。
        """
        material_id = board.upload(board.owner.token).json()["data"]["material"]["id"]
        keeper = board.upload(board.owner.token).json()["data"]["material"]["id"]
        written = board.teach_space(board.owner.token, [material_id, keeper])
        assert written.status_code == 200, written.text

        gone = board.client.delete(
            f"/materials/{material_id}", headers=_auth(board.owner.token)
        )
        assert gone.status_code == 204, gone.text

        counts = board.counts(board.owner.token)
        assert material_id not in counts  # 删掉的那一行整行都不在了
        assert counts[keeper] == 1  # 悬空那位没把它的数顶掉

    def test_soft_deleted_rows_stop_counting(self, board: _Board) -> None:
        """撤下来的项目集、删掉的赛题，它们那一格 JSON 还在库里，但不该再算。

        老师在界面上看不见它们、也改不了，列着它们的 id 只会把这一格撑大。
        """
        in_task = board.upload(board.owner.token).json()["data"]["material"]["id"]
        in_category = board.upload(board.owner.token).json()["data"]["material"]["id"]
        kept = board.upload(board.owner.token).json()["data"]["material"]["id"]

        task_id = board.teach_task(board.owner.token, [in_task, kept])
        category_id = board.teach_category(board.owner.token, [in_category, kept])
        assert board.teach_space(board.owner.token, [kept]).status_code == 200
        assert board.counts(board.owner.token) == {
            in_task: 1,
            in_category: 1,
            kept: 3,
        }

        assert board.drop_task(board.owner.token, task_id).status_code == 204
        assert board.drop_category(board.owner.token, category_id).status_code == 204

        counts = board.counts(board.owner.token)
        assert counts[in_task] == 0  # 题删了，它那一处跟着不算
        assert counts[in_category] == 0  # 项目集撤了，同上
        assert counts[kept] == 1  # 只剩空间默认那一处
