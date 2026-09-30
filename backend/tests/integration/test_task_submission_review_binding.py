"""一条评审只能沿它自己的题与报名走到 —— ``submissionId`` 不是一个主键。

五条评审路由（GET/POST/PATCH/PUT/DELETE）共挂在同一个路径上：

    /tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review

从前的实现把 ``participantId`` 丢掉、鉴权只看 path 的 ``taskId``，于是
``submissionId`` 成了全局自由主键：任何在**别的**题上通过 ``may_teach_task`` 的人
（板上成员在板上发一道题就是那道题的出题者）都能给别的课上的任意提交打分、改分、
删分；GET 更是连鉴权都没有，知道一个 id 就能读到它的成绩与评语。

这里按浏览器会收到的状态码断言三件事：

1. 错配的 id 一律 404 —— 路径没指向任何东西，不替调用者确认 id 存在；
2. 能读这条提交的人才读得到它的评审（出题人 / 板的管理员 / 提交者本人），
   而在这条路径上没资格的人读不到；
3. 正常路径一条都没少：出题人、板的管理员、提交者本人该 200 的仍然 200。

攻击者与被害者必须落在**两块不同的板**上：建板的人就是这块板的 OWNER（管理员），
若两道题同在一块板里，攻击者靠「板的管理员」这一条就真的教得了那道题，越权便测不
出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)

# 原评审的取值：任何一次越权尝试之后，这三个字段都必须原样还在这儿。
_ORIGINAL = {"accepted": True, "score": 90, "comment": "原评审"}
# 越权尝试写入的取值：与上面不同，改没改过一眼可辨。
_ATTACK = {"accepted": False, "score": 1, "comment": "不该落地的评审"}


def _auth(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _human(user_client: UserCreator, api_client: TestClient) -> CreatedUser:
    user = user_client.create_user()
    user.token = _login(user_client, api_client, user)
    return user


def _new_board(user_client: UserCreator, api_client: TestClient, label: str):
    """一块已通过审核的板，以及建它的人（OWNER = 这块板的管理员）。"""
    owner = _human(user_client, api_client)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Review Binding {label} ({suffix})",
            "intro": "一门课",
            "description": "一块题目板。" * 20,
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_auth(owner.token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return owner, {
        "space_id": space["id"],
        "category_id": space["defaultCategoryId"],
    }


def _publish_task(
    api_client: TestClient, owner: CreatedUser, board: dict, label: str
) -> int:
    suffix = unique_int(10000000, 99999999)
    deadline = int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000)
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"Review Binding Task {label} ({suffix})",
            "submitterType": "USER",
            "deadline": deadline,
            "resubmittable": False,
            "editable": False,
            "intro": "题目简介 " * 10,
            "description": "题目描述 " * 10,
            "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
            "space": board["space_id"],
            "categoryId": board["category_id"],
        },
        headers=_auth(owner.token),
    )
    assert resp.status_code == 200, resp.text
    task_id: int = resp.json()["data"]["task"]["id"]

    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(owner.token),
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _join(api_client: TestClient, actor: CreatedUser, task_id: int) -> int:
    joined = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": actor.user_id},
        json={},
        headers=_auth(actor.token),
    )
    assert joined.status_code == 200, joined.text
    return int(joined.json()["data"]["participant"]["id"])


def _approve_member(
    api_client: TestClient, by: CreatedUser, task_id: int, membership_id: int
) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(by.token),
    )
    assert resp.status_code == 200, resp.text


def _submit(
    api_client: TestClient, actor: CreatedUser, task_id: int, membership_id: int
) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions",
        json=[{"text": "这是我的提交。"}],
        headers=_auth(actor.token),
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["submission"]["id"])


def _review_url(task_id: int, participant_id: int, submission_id: int) -> str:
    return (
        f"/tasks/{task_id}/participants/{participant_id}"
        f"/submissions/{submission_id}/review"
    )


@dataclass
class _Scene:
    attacker: CreatedUser  # 能教自己的题，别人的题不是他的
    attacker_task: int
    attacker_membership: int
    teacher: CreatedUser  # 被害那道题的出题人（也是那块板的 OWNER）
    admin: CreatedUser  # 那块板的管理员，不是出题人
    victim: CreatedUser  # 提交者本人
    victim_task: int
    victim_membership: int
    victim_submission: int
    bystander: CreatedUser  # 同一块板里的普通成员，也提交了，但没有评审
    bystander_membership: int
    bystander_submission: int
    stranger: CreatedUser  # 与这块板毫无关系的人


@pytest.fixture
def scene(user_client: UserCreator, api_client: TestClient) -> _Scene:
    attacker, board_a = _new_board(user_client, api_client, "A")
    attacker_task = _publish_task(api_client, attacker, board_a, "A")
    # 攻击者是自己这道题里的报名 —— 越权路径上那个 participantId 就用它。
    attacker_membership = _join(api_client, attacker, attacker_task)

    teacher, board_b = _new_board(user_client, api_client, "B")
    victim_task = _publish_task(api_client, teacher, board_b, "B")

    admin = _human(user_client, api_client)
    made_admin = api_client.post(
        f"/spaces/{board_b['space_id']}/managers",
        json={"userId": admin.user_id, "role": "ADMIN"},
        headers=_auth(teacher.token),
    )
    assert made_admin.status_code == 201, made_admin.text

    victim = _human(user_client, api_client)
    victim_membership = _join(api_client, victim, victim_task)
    _approve_member(api_client, teacher, victim_task, victim_membership)
    victim_submission = _submit(api_client, victim, victim_task, victim_membership)

    bystander = _human(user_client, api_client)
    bystander_membership = _join(api_client, bystander, victim_task)
    _approve_member(api_client, teacher, victim_task, bystander_membership)
    bystander_submission = _submit(
        api_client, bystander, victim_task, bystander_membership
    )

    stranger = _human(user_client, api_client)

    return _Scene(
        attacker=attacker,
        attacker_task=attacker_task,
        attacker_membership=attacker_membership,
        teacher=teacher,
        admin=admin,
        victim=victim,
        victim_task=victim_task,
        victim_membership=victim_membership,
        victim_submission=victim_submission,
        bystander=bystander,
        bystander_membership=bystander_membership,
        bystander_submission=bystander_submission,
        stranger=stranger,
    )


def _read_review(api_client: TestClient, url: str, token: str | None) -> dict:
    resp = api_client.get(url, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["review"]


class TestAForeignSubmissionIdIsNotAKey:
    """``submissionId`` 单拎出来什么都不是：它只能沿 path 的题与报名走到。"""

    def test_grading_another_tasks_submission_leaves_it_untouched(
        self, scene: _Scene, api_client: TestClient
    ):
        """攻击者把 path 的题换成自己教得动的那道，submissionId 指向别人的提交。

        这条路径从前是通的：participantId 被丢掉，鉴权只问 path 那道题，于是
        POST 会在别人的提交上真的建出一条评审，PATCH/PUT/DELETE 会真的改动、删掉
        它。现在四个方法都必须 404，且那条评审原样还在。
        """
        url = _review_url(
            scene.victim_task, scene.victim_membership, scene.victim_submission
        )
        created = api_client.post(
            url, json=dict(_ORIGINAL), headers=_auth(scene.teacher.token)
        )
        assert created.status_code == 200, created.text
        assert created.json()["data"]["review"]["detail"] == _ORIGINAL

        # 两种写法都要挡住：报上自己在攻击者那道题里的报名，或者干脆写上受害者
        # 自己的 participantId —— 只要 path 的题不是提交所属的题，就什么都不是。
        paths = [
            _review_url(
                scene.attacker_task, scene.attacker_membership, scene.victim_submission
            ),
            _review_url(
                scene.attacker_task, scene.victim_membership, scene.victim_submission
            ),
        ]
        for url in paths:
            for method in ("post", "patch", "put", "delete"):
                kwargs = {} if method == "delete" else {"json": dict(_ATTACK)}
                resp = getattr(api_client, method)(
                    url, headers=_auth(scene.attacker.token), **kwargs
                )
                assert resp.status_code == 404, (
                    f"{method.upper()} {url} -> {resp.status_code}: {resp.text}"
                )

        # 成绩与评语原样；DELETE 也没把它删掉（reviewed 仍是 True）。
        after = _read_review(
            api_client,
            _review_url(
                scene.victim_task, scene.victim_membership, scene.victim_submission
            ),
            scene.teacher.token,
        )
        assert after["reviewed"] is True
        assert after["detail"] == _ORIGINAL

    def test_creating_a_review_through_a_foreign_task_writes_nothing(
        self, scene: _Scene, api_client: TestClient
    ):
        """别人的提交上还没有评审时，越权的 POST 从前会真的写出一条 —— 现在 404。

        这条用的是另一份提交（同一道题里的另一位报名者），它本来就没有评审，所以
        「没写进去」是可验证的：出题者去读它仍然是 404。
        """
        resp = api_client.post(
            _review_url(
                scene.attacker_task,
                scene.attacker_membership,
                scene.bystander_submission,
            ),
            json=dict(_ATTACK),
            headers=_auth(scene.attacker.token),
        )
        assert resp.status_code == 404, f"{resp.status_code}: {resp.text}"

        untouched = api_client.get(
            _review_url(
                scene.victim_task,
                scene.bystander_membership,
                scene.bystander_submission,
            ),
            headers=_auth(scene.teacher.token),
        )
        assert untouched.status_code == 404, untouched.text

    def test_a_participant_id_from_the_same_task_is_not_the_submitter(
        self, scene: _Scene, api_client: TestClient
    ):
        """同一道题里的另一个人也不是这条提交的主人：报名与提交必须对得上。

        这一档是「路径自洽」与「路径错配」的分界 —— 题是真的、报名也是真的，只是
        这个报名不是那份提交的报名，所以整条路径仍然什么都没指向（404），而不是
        403。
        """
        url = _review_url(
            scene.victim_task, scene.bystander_membership, scene.victim_submission
        )
        for method in ("get", "post", "patch", "put", "delete"):
            kwargs = {} if method in ("get", "delete") else {"json": dict(_ATTACK)}
            resp = getattr(api_client, method)(
                url, headers=_auth(scene.teacher.token), **kwargs
            )
            assert resp.status_code == 404, (
                f"{method.upper()} {url} -> {resp.status_code}: {resp.text}"
            )

    def test_ids_that_name_nothing_are_not_found(
        self, scene: _Scene, api_client: TestClient
    ):
        """不存在的 task / participant / submission 同一个口径：404。"""
        urls = [
            _review_url(
                scene.victim_task, scene.victim_membership, 2000000000
            ),  # 没有这份提交
            _review_url(scene.victim_task, 2000000000, scene.victim_submission),
            _review_url(2000000000, scene.victim_membership, scene.victim_submission),
        ]
        for url in urls:
            for method in ("get", "post", "patch", "put", "delete"):
                kwargs = {} if method in ("get", "delete") else {"json": dict(_ATTACK)}
                resp = getattr(api_client, method)(
                    url, headers=_auth(scene.teacher.token), **kwargs
                )
                assert resp.status_code == 404, (
                    f"{method.upper()} {url} -> {resp.status_code}: {resp.text}"
                )


class TestReadingAReview:
    """能读这条提交的人才读得到它的评审 —— 从前这里连鉴权都没有。"""

    def test_reading_a_review_takes_reading_its_submission(
        self, scene: _Scene, api_client: TestClient
    ):
        """出题人 / 板的管理员 / 提交者本人：200；其余在读得通的路径上：403。

        路径自洽而人不自洽时答 403（而不是 404）：这条路是真实存在的，403 说的才是
        「你没资格」。
        """
        url = _review_url(
            scene.victim_task, scene.victim_membership, scene.victim_submission
        )
        api_client.post(url, json=dict(_ORIGINAL), headers=_auth(scene.teacher.token))

        for allowed in (scene.teacher, scene.admin, scene.victim):
            review = _read_review(api_client, url, allowed.token)
            assert review["reviewed"] is True
            assert review["detail"] == _ORIGINAL

        # 攻击者在自己的题上通过鉴权，但这条提交不是他这道题的。
        for refused in (scene.attacker, scene.bystander, scene.stranger):
            resp = api_client.get(url, headers=_auth(refused.token))
            assert resp.status_code == 403, f"{resp.status_code}: {resp.text}"


class TestTheNormalPathsStillWork:
    """能读的人也是能写的人：老师的四个方法、本人的读，一条都没少。"""

    def test_the_author_and_the_board_admin_still_review(
        self, scene: _Scene, api_client: TestClient
    ):
        url = _review_url(
            scene.victim_task, scene.victim_membership, scene.victim_submission
        )

        created = api_client.post(
            url, json=dict(_ORIGINAL), headers=_auth(scene.teacher.token)
        )
        assert created.status_code == 200, created.text
        assert created.json()["data"]["review"]["detail"] == _ORIGINAL

        patched = api_client.patch(
            url,
            json={"score": 95, "comment": "板的管理员改的"},
            headers=_auth(scene.admin.token),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["data"]["review"]["detail"]["score"] == 95

        # PUT 是全量替换：走的是同一条路径、同一个鉴权。
        replaced = api_client.put(
            url, json=dict(_ATTACK), headers=_auth(scene.admin.token)
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["data"]["review"]["detail"] == _ATTACK

        # 提交者本人读得到自己的评审（哪怕是管理员刚改过的）。
        assert _read_review(api_client, url, scene.victim.token)["detail"] == _ATTACK

        deleted = api_client.delete(url, headers=_auth(scene.admin.token))
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"]["review"]["reviewed"] is False

        # 删掉之后可以重新建 —— 越权那条路被挡了，正常这条路没有被顺手挡掉。
        again = api_client.post(
            url, json=dict(_ORIGINAL), headers=_auth(scene.teacher.token)
        )
        assert again.status_code == 200, again.text
        assert _read_review(api_client, url, scene.teacher.token)["detail"] == _ORIGINAL
