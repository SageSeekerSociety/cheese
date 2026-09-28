"""一个回答属于一道题 —— URL 里的父级 id 是地址的一部分，不是装饰。

`/questions/{question_id}/answers/{answer_id}/...` 这一族里，两半的口径原本不一致：

* 读（也是改、删）那三条 —— `GET` / `PUT` / `DELETE /questions/{q}/answers/{a}` ——
  早就把 URL 里的 `question_id` 和回答自己的 `question_id` 对上，不匹配回 404；
* 挂在同一个前缀下的另外 9 条 —— 投票（`POST` / `DELETE` / `GET .../vote`）、
  评论（`GET` / `POST` / `DELETE .../comments`）、收藏（`PUT` / `DELETE
  .../favorite`）、态度（`POST .../attitudes`）—— 函数体里只有一句
  `_ = question_id`（`delete_answer_comment` 还多一句 `_ = answer_id`），父级 id
  读进来就丢掉。

于是同一个 `answer_id` 挂在任意一道题 —— 甚至根本不存在的题 —— 下面都回 200；
`DELETE` 那条更彻底，认的只有 `comment_id`，父级两道 id 一起丢，评论真的被删掉。
同一个形状还有 `DELETE /questions/{q}/comments/{c}`（`questions.py` 里的
`delete_question_comment`）。

本文件钉的只有「绑定与 404」这一件事，两条判据：

1. **父级 id 与被寻址对象不匹配（含父对象不存在）→ 404**，而且回包与同文件读路由
   那句 404 **逐字相同**（`test_a_mismatched_parent_answers_like_the_read_routes`）。
   404 而不是 403：错配的 id 不指向任何东西，403 会承认「这个回答存在，只是不在这
   道题上」（口径同 `test_an_invitation_belongs_to_its_question.py`）。
2. **父级 id 正确 → 行为一字不变，而且副作用真的发生了**：票投上了、评论真的被
   删掉了、收藏真的落库了。绑父级不能顺手把功能打瘸。

父级 id 从来不是权限闸门（投票看投票人身份、删评论看评论作者），所以这里一个字
的授权都不动：`test_only_the_comment_author_still_deletes_it` 把这条口径也钉住。
"""

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator, unique_int

#: 一个不会存在的 id。题目和回答的 id 都是自增序列发出来的小整数。
_NOWHERE = 987_654_321


def _new_person(
    user_client: UserCreator, api_client: TestClient
) -> tuple[CreatedUser, dict[str, str]]:
    """一个真登录的人 + 他的 Authorization 头。"""
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user, {"Authorization": f"Bearer {user.token}"}


def _create_question(client: TestClient, headers: dict[str, str]) -> int:
    response = client.post(
        "/questions",
        headers=headers,
        json={
            "title": f"Answer parent {unique_int(100000, 999999)}",
            "content": "这个回答挂在哪个题目上？",
            "type": 0,
            "topics": [],
            "groupId": None,
            "bounty": 0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_answer(
    client: TestClient, headers: dict[str, str], question_id: int
) -> int:
    response = client.post(
        f"/questions/{question_id}/answers",
        headers=headers,
        json={"content": f"回答正文 {unique_int(100000, 999999)}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_answer_comment(
    client: TestClient, headers: dict[str, str], question_id: int, answer_id: int
) -> int:
    response = client.post(
        f"/questions/{question_id}/answers/{answer_id}/comments",
        headers=headers,
        json={"content": "挂在那个回答上的一条评论"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["comment"]["id"]


def _create_question_comment(
    client: TestClient, headers: dict[str, str], question_id: int
) -> int:
    response = client.post(
        f"/questions/{question_id}/comments",
        headers=headers,
        json={"content": "挂在那个题目上的一条评论"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["comment"]["id"]


def _answer_comment_ids(
    client: TestClient, headers: dict[str, str], question_id: int, answer_id: int
) -> list[int]:
    response = client.get(
        f"/questions/{question_id}/answers/{answer_id}/comments", headers=headers
    )
    assert response.status_code == 200, response.text
    return [comment["id"] for comment in response.json()["data"]["comments"]]


def _answer_is_favorite(
    client: TestClient, headers: dict[str, str], question_id: int, answer_id: int
) -> bool:
    response = client.get(
        f"/questions/{question_id}/answers/{answer_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    return bool(response.json()["data"]["answer"]["is_favorite"])


def _attitudes(
    client: TestClient, headers: dict[str, str], question_id: int, answer_id: int
) -> dict:
    response = client.get(
        f"/questions/{question_id}/answers/{answer_id}/vote", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@dataclass
class Scene:
    """两道路（A、B）、一个挂在 A 上的回答、挂在那个回答上的一条评论。"""

    client: TestClient
    author: dict[str, str]
    answerer: dict[str, str]
    commenter: dict[str, str]
    stranger: dict[str, str]
    question_a: int
    question_b: int
    answer_id: int
    comment_id: int

    def hit(self, method: str, path: str, headers: dict[str, str], **kwargs):
        return self.client.request(method, path, headers=headers, **kwargs)


@pytest.fixture
def scene(api_client: TestClient, user_client: UserCreator) -> Scene:
    _, author = _new_person(user_client, api_client)
    _, answerer = _new_person(user_client, api_client)
    _, commenter = _new_person(user_client, api_client)
    _, stranger = _new_person(user_client, api_client)

    question_a = _create_question(api_client, author)
    question_b = _create_question(api_client, author)
    answer_id = _create_answer(api_client, answerer, question_a)
    comment_id = _create_answer_comment(api_client, commenter, question_a, answer_id)
    return Scene(
        client=api_client,
        author=author,
        answerer=answerer,
        commenter=commenter,
        stranger=stranger,
        question_a=question_a,
        question_b=question_b,
        answer_id=answer_id,
        comment_id=comment_id,
    )


def _wrong_parents(scene: Scene, tail: str) -> dict[str, str]:
    """同一个尾巴挂到三种错的父级下面：别的题 / 不存在的题 / 不存在的回答。"""
    return {
        "别的题": f"/questions/{scene.question_b}/answers/{scene.answer_id}/{tail}",
        "题不存在": f"/questions/{_NOWHERE}/answers/{scene.answer_id}/{tail}",
        "回答不存在": f"/questions/{scene.question_a}/answers/{_NOWHERE}/{tail}",
    }


def _right_parent(scene: Scene, tail: str) -> str:
    return f"/questions/{scene.question_a}/answers/{scene.answer_id}/{tail}"


# --- 1. 绑定：父级 id 错（含父对象不存在）→ 404，且什么都没发生 -----------------


def test_a_wrong_parent_id_votes_on_nothing(scene: Scene):
    """`POST .../vote`：走错题、题不存在、回答不存在都 404，票一张没投上。"""
    for door, path in _wrong_parents(scene, "vote").items():
        response = scene.hit(
            "POST",
            path,
            scene.stranger,
            json={"voteType": "POSITIVE"},
        )
        assert response.status_code == 404, f"{door}: {response.text}"

    before = _attitudes(scene.client, scene.answerer, scene.question_a, scene.answer_id)
    assert before["upvotes"] == 0
    assert before["downvotes"] == 0

    # 对照组：父级 id 对 → 200，票真的投上了（绑定不能把功能打瘸）。
    response = scene.hit(
        "POST",
        _right_parent(scene, "vote"),
        scene.stranger,
        json={"voteType": "POSITIVE"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["upvotes"] == 1
    latter = _attitudes(scene.client, scene.stranger, scene.question_a, scene.answer_id)
    assert latter["upvotes"] == 1
    assert latter["userVote"] == "POSITIVE"


def test_a_wrong_parent_id_removes_no_vote(scene: Scene):
    """`DELETE .../vote`：父级 id 错了撤不掉票。"""
    voted = scene.hit(
        "POST",
        _right_parent(scene, "vote"),
        scene.stranger,
        json={"voteType": "POSITIVE"},
    )
    assert voted.status_code == 200, voted.text

    for door, path in _wrong_parents(scene, "vote").items():
        response = scene.hit("DELETE", path, scene.stranger)
        assert response.status_code == 404, f"{door}: {response.text}"

    # 票还在。
    still = _attitudes(scene.client, scene.stranger, scene.question_a, scene.answer_id)
    assert still["upvotes"] == 1
    assert still["userVote"] == "POSITIVE"

    # 对照组：父级 id 对 → 200，票真的撤掉了。
    response = scene.hit("DELETE", _right_parent(scene, "vote"), scene.stranger)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["userVote"] is None
    gone = _attitudes(scene.client, scene.stranger, scene.question_a, scene.answer_id)
    assert gone["upvotes"] == 0
    assert gone["userVote"] is None


def test_a_wrong_parent_id_reads_no_vote(scene: Scene):
    """`GET .../vote`：父级 id 错了读不到这个回答的票。"""
    voted = scene.hit(
        "POST",
        _right_parent(scene, "vote"),
        scene.stranger,
        json={"voteType": "NEGATIVE"},
    )
    assert voted.status_code == 200, voted.text

    for door, path in _wrong_parents(scene, "vote").items():
        response = scene.hit("GET", path, scene.stranger)
        assert response.status_code == 404, f"{door}: {response.text}"

    # 对照组：父级 id 对 → 200，读得到那一票。
    response = scene.hit("GET", _right_parent(scene, "vote"), scene.stranger)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["downvotes"] == 1
    assert response.json()["data"]["userVote"] == "NEGATIVE"


def test_a_wrong_parent_id_reads_no_comments(scene: Scene):
    """`GET .../comments`：父级 id 错了读不到挂在这个回答上的评论。"""
    for door, path in _wrong_parents(scene, "comments").items():
        response = scene.hit("GET", path, scene.stranger)
        assert response.status_code == 404, f"{door}: {response.text}"
        # 回的是「没有」，不是「有但不给你」：没有列表信封，也没有那条评论的正文。
        assert "comments" not in response.text
        assert "挂在那个回答上的一条评论" not in response.text

    # 对照组：父级 id 对 → 200，那条评论就在列表里。
    ids = _answer_comment_ids(
        scene.client, scene.stranger, scene.question_a, scene.answer_id
    )
    assert scene.comment_id in ids


def test_a_wrong_parent_id_creates_no_comment(scene: Scene):
    """`POST .../comments`：父级 id 错了不许在别处落下一条评论。"""
    for door, path in _wrong_parents(scene, "comments").items():
        response = scene.hit(
            "POST", path, scene.stranger, json={"content": f"走错门 {door}"}
        )
        assert response.status_code == 404, f"{door}: {response.text}"
        assert "走错门" not in response.text

    # 一条都没落下来：这个回答下面还是只有原来那条。
    ids = _answer_comment_ids(
        scene.client, scene.stranger, scene.question_a, scene.answer_id
    )
    assert ids == [scene.comment_id]

    # 对照组：父级 id 对 → 201，评论真的写进去了。
    created = scene.hit(
        "POST",
        _right_parent(scene, "comments"),
        scene.stranger,
        json={"content": "这条该写上"},
    )
    assert created.status_code == 201, created.text
    new_id = created.json()["data"]["comment"]["id"]
    assert _answer_comment_ids(
        scene.client, scene.stranger, scene.question_a, scene.answer_id
    ) == [scene.comment_id, new_id]


def test_a_wrong_parent_id_deletes_no_comment(scene: Scene):
    """`DELETE .../comments/{c}`：题写错、回答写错（含不存在的回答）都删不掉。

    这条最狠：它以前把 `question_id` 和 `answer_id` **两句一起丢**，只按
    `comment_id` + 评论作者判权，所以拿一道不存在的题/一个不存在的回答做前缀
    照样把评论删掉。
    """
    other_answer = _create_answer(scene.client, scene.author, scene.question_a)
    wrong_parents = {
        "别的题": f"/questions/{scene.question_b}/answers/{scene.answer_id}"
        f"/comments/{scene.comment_id}",
        "题不存在": f"/questions/{_NOWHERE}/answers/{scene.answer_id}"
        f"/comments/{scene.comment_id}",
        "回答不存在": f"/questions/{scene.question_a}/answers/{_NOWHERE}"
        f"/comments/{scene.comment_id}",
        # 题是对的、回答也是真的，但这条评论挂在**另一个**回答上。
        "另一个回答": f"/questions/{scene.question_a}/answers/{other_answer}"
        f"/comments/{scene.comment_id}",
    }
    for door, path in wrong_parents.items():
        response = scene.hit("DELETE", path, scene.commenter)
        assert response.status_code == 404, f"{door}: {response.text}"

    # 评论还在。
    ids = _answer_comment_ids(
        scene.client, scene.commenter, scene.question_a, scene.answer_id
    )
    assert scene.comment_id in ids

    # 对照组：父级 id 对 → 200，评论真的删掉了。
    response = scene.hit(
        "DELETE",
        _right_parent(scene, "comments") + f"/{scene.comment_id}",
        scene.commenter,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["deleted"] is True
    assert scene.comment_id not in _answer_comment_ids(
        scene.client, scene.commenter, scene.question_a, scene.answer_id
    )


def test_a_wrong_parent_id_favorites_nothing(scene: Scene):
    """`PUT .../favorite`：父级 id 错了收藏不落库。"""
    for door, path in _wrong_parents(scene, "favorite").items():
        response = scene.hit("PUT", path, scene.stranger)
        assert response.status_code == 404, f"{door}: {response.text}"

    assert (
        _answer_is_favorite(
            scene.client, scene.stranger, scene.question_a, scene.answer_id
        )
        is False
    )

    # 对照组：父级 id 对 → 200，收藏真的落库了。
    response = scene.hit("PUT", _right_parent(scene, "favorite"), scene.stranger)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["isFavorited"] is True
    assert (
        _answer_is_favorite(
            scene.client, scene.stranger, scene.question_a, scene.answer_id
        )
        is True
    )


def test_a_wrong_parent_id_unfavorites_nothing(scene: Scene):
    """`DELETE .../favorite`：父级 id 错了取消不掉收藏。"""
    favorited = scene.hit("PUT", _right_parent(scene, "favorite"), scene.stranger)
    assert favorited.status_code == 200, favorited.text

    for door, path in _wrong_parents(scene, "favorite").items():
        response = scene.hit("DELETE", path, scene.stranger)
        assert response.status_code == 404, f"{door}: {response.text}"

    assert (
        _answer_is_favorite(
            scene.client, scene.stranger, scene.question_a, scene.answer_id
        )
        is True
    )

    # 对照组：父级 id 对 → 200，收藏真的取消了。
    response = scene.hit("DELETE", _right_parent(scene, "favorite"), scene.stranger)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["isFavorited"] is False
    assert (
        _answer_is_favorite(
            scene.client, scene.stranger, scene.question_a, scene.answer_id
        )
        is False
    )


def test_a_wrong_parent_id_sets_no_attitude(scene: Scene):
    """`POST .../attitudes`：父级 id 错了态度不落库。"""
    for door, path in _wrong_parents(scene, "attitudes").items():
        response = scene.hit(
            "POST", path, scene.stranger, json={"attitude_type": "POSITIVE"}
        )
        assert response.status_code == 404, f"{door}: {response.text}"

    votes = _attitudes(scene.client, scene.stranger, scene.question_a, scene.answer_id)
    assert votes["upvotes"] == 0

    # 对照组：父级 id 对 → 200，态度真的写进去了。
    response = scene.hit(
        "POST",
        _right_parent(scene, "attitudes"),
        scene.stranger,
        json={"attitude_type": "POSITIVE"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["attitudes"]["positive_count"] == 1
    assert response.json()["data"]["attitudes"]["user_attitude"] == "POSITIVE"


def test_a_wrong_parent_id_deletes_no_question_comment(scene: Scene):
    """`DELETE /questions/{q}/comments/{c}`：同一个形状，题目评论也认父级 id。

    这条在 `questions.py` 里，同样只有一句 `_ = question_id`：拿别的题（哪怕不存在
    的题）做前缀，照样能把一条题目评论删掉。
    """
    comment_id = _create_question_comment(
        scene.client, scene.commenter, scene.question_a
    )

    for door, path in {
        "别的题": f"/questions/{scene.question_b}/comments/{comment_id}",
        "题不存在": f"/questions/{_NOWHERE}/comments/{comment_id}",
    }.items():
        response = scene.hit("DELETE", path, scene.commenter)
        assert response.status_code == 404, f"{door}: {response.text}"

    # 评论还在原题下面。
    listed = scene.client.get(
        f"/questions/{scene.question_a}/comments", headers=scene.commenter
    )
    assert listed.status_code == 200, listed.text
    assert comment_id in [c["id"] for c in listed.json()["data"]["comments"]]

    # 对照组：父级 id 对 → 200，评论真的删掉了。
    response = scene.hit(
        "DELETE",
        f"/questions/{scene.question_a}/comments/{comment_id}",
        scene.commenter,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["deleted"] is True
    listed = scene.client.get(
        f"/questions/{scene.question_a}/comments", headers=scene.commenter
    )
    assert comment_id not in [c["id"] for c in listed.json()["data"]["comments"]]


# --- 2. 回包与同文件读路由逐字一致 ---------------------------------------------


def test_a_mismatched_parent_answers_like_the_read_routes(scene: Scene):
    """错配的父级 id，写路由回的就是 `get_answer` 那句 404，逐字相同。

    对照组是同一族里早就有绑定的 `GET /questions/{q}/answers/{a}`：它回
    `NotFoundError("Answer not found for this question")`。少一个字段、多一层
    信封、把 message 换成别的，都不算「和对照组一致」—— 两条路一旦答得不一样，
    那个差别本身就是一个探针。
    """
    control = scene.client.get(
        f"/questions/{scene.question_b}/answers/{scene.answer_id}",
        headers=scene.answerer,
    )
    assert control.status_code == 404, control.text
    expected = control.json()

    wrong = f"/questions/{scene.question_b}/answers/{scene.answer_id}"
    answers = {
        "vote": scene.hit(
            "POST", f"{wrong}/vote", scene.stranger, json={"voteType": "POSITIVE"}
        ),
        "remove vote": scene.hit("DELETE", f"{wrong}/vote", scene.stranger),
        "get votes": scene.hit("GET", f"{wrong}/vote", scene.stranger),
        "list comments": scene.hit("GET", f"{wrong}/comments", scene.stranger),
        "create comment": scene.hit(
            "POST", f"{wrong}/comments", scene.stranger, json={"content": "x"}
        ),
        "delete comment": scene.hit(
            "DELETE", f"{wrong}/comments/{scene.comment_id}", scene.commenter
        ),
        "favorite": scene.hit("PUT", f"{wrong}/favorite", scene.stranger),
        "unfavorite": scene.hit("DELETE", f"{wrong}/favorite", scene.stranger),
        "attitude": scene.hit(
            "POST",
            f"{wrong}/attitudes",
            scene.stranger,
            json={"attitude_type": "POSITIVE"},
        ),
    }
    for door, response in answers.items():
        assert response.status_code == 404, f"{door}: {response.text}"
        assert response.json() == expected, f"{door}: {response.text}"


# --- 3. 授权口径没动 -----------------------------------------------------------


def test_only_the_comment_author_still_deletes_it(scene: Scene):
    """父级 id 是对的、但你不是评论作者 —— 还是 403，不是 404。

    绑父级不是授权修复：删评论的闸门一直是「你是不是评论作者」。这里把那条口径
    原样钉住，免得「补 404」被人顺手改成「收紧成 403」或者反过来。
    """
    response = scene.hit(
        "DELETE",
        _right_parent(scene, "comments") + f"/{scene.comment_id}",
        scene.stranger,
    )
    assert response.status_code == 403, response.text
    assert scene.comment_id in _answer_comment_ids(
        scene.client, scene.commenter, scene.question_a, scene.answer_id
    )

    # 匿名调用者照旧 401（缺的是凭据，不是权限）。
    anonymous = scene.client.delete(
        _right_parent(scene, "comments") + f"/{scene.comment_id}"
    )
    assert anonymous.status_code == 401, anonymous.text
