"""一张邀请属于一个题目 —— URL 里的 question_id 是地址的一部分，不是装饰。

「问题-邀请」这一族有三条读路由，它们以前既不问「你是谁」，也不看 URL 里那个
`question_id`：

* `GET /questions/{q}/invitations` —— 整个被邀请人名单（谁被邀请、答没答）；
* `GET /questions/{q}/invitations/recommendations` —— 全站用户目录前 N 个；
* `GET /questions/{q}/invitations/{i}` —— 一张邀请，**只认 `invitation_id`**
  （函数体里那句 `_ = question_id` 就是这件事：父级 id 被读进来然后丢掉）。

于是不带任何凭据、只顺着小整数 id 就能拼出「谁被邀请去答了哪些题、答没答」。这个
文件钉两点，两点都只看浏览器收到的东西：

1. **要登录**：三条路由匿名都是 401。同族的 `POST`（建邀请）和 `DELETE`（撤回）
   早就都有 `require_auth_user`（`test_cancel_invitation_no_auth` 钉着那个 401），
   读侧跟它们走。401 而不是 403：缺的是凭据，不是权限 —— 这一族里没有哪张邀请是
   「登录了也不许看」的（见下一条）。
2. **父级 id 要绑定**：`question_id != invitation.question_id` → 404。错配的 id
   不指向任何东西，所以是 404 而不是 403：403 会承认「这个邀请存在，只是不给你
   看」，把存在性漏出去。`DELETE` 那条同样绑定（它以前把别人的邀请按错题的路径
   删得掉，因为它认的也只有 `invitation_id`）。

授权口径是**任何登录用户**，不是「题目作者」或「被邀请人」二选一，理由在
`test_a_logged_in_stranger_reads_the_same_list` 里：这个功能的三个入口
（`InvitationList.vue` 的列表、推荐、建邀请）在页面上对**任何**能看见这张题的人
都开着（`Detail.vue` 的邀请对话框只要题目有赏金、没采纳答案就出现，没有作者判断），
后端 `create_invitation` 也只问「登录了没有」。所以「谁能看」和「谁能发」在这个功能
里是同一件事；把读侧收成「只有作者」会当场把非作者页面上的对话框打成 403，那是改
产品口径，不是修权限。今天没有任何公开（免登录）入口读邀请：详情那条全仓库没有
调用方，列表和推荐只从登录后的页面里调。
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator, unique_int


def _create_question(client: TestClient, headers: dict[str, str]) -> int:
    response = client.post(
        "/questions",
        headers=headers,
        json={
            "title": f"Invitation parent {unique_int(100000, 999999)}",
            "content": "这张邀请挂在哪个题目上？",
            "type": 0,
            "topics": [],
            "groupId": None,
            "bounty": 0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _invite(
    client: TestClient, headers: dict[str, str], question_id: int, user_id: int
) -> int:
    response = client.post(
        f"/questions/{question_id}/invitations",
        headers=headers,
        json={"user_id": user_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["invitationId"]


def _headers(user: CreatedUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def author(
    user_client: UserCreator, api_client: TestClient
) -> tuple[CreatedUser, dict[str, str]]:
    """出题人：一个真登录的人，他的题上挂着一张邀请。"""
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user, _headers(user)


@pytest.fixture
def invitee(user_client: UserCreator, api_client: TestClient) -> CreatedUser:
    """被邀请的那个人 —— 另一个真登录的人。"""
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _three_doors(question_id: int, invitation_id: int) -> dict[str, str]:
    """三条读路由，一一对应审计里那三条。"""
    base = f"/questions/{question_id}/invitations"
    return {
        "list": base,
        "recommendations": f"{base}/recommendations",
        "detail": f"{base}/{invitation_id}",
    }


# --- 1. 要登录 --------------------------------------------------------------


def test_an_anonymous_caller_reads_no_invitation(
    api_client: TestClient,
    author: tuple[CreatedUser, dict[str, str]],
    invitee: CreatedUser,
):
    """三条路由一个都不放过匿名调用者，401 —— 同一族写路由的口径。

    这条以前是**泄漏本身**：不带 Authorization 头，三条都回 200，详情那条还把
    「邀请体」（被邀请人的 profile 和答没答）一起给了出来。
    """
    _, headers = author
    question_id = _create_question(api_client, headers)
    invitation_id = _invite(api_client, headers, question_id, invitee.user_id)

    for door, path in _three_doors(question_id, invitation_id).items():
        response = api_client.get(path)
        assert response.status_code == 401, f"{door}: {response.text}"


# --- 2. 父级 id -------------------------------------------------------------


def test_a_wrong_parent_id_shows_nothing(
    api_client: TestClient,
    author: tuple[CreatedUser, dict[str, str]],
    invitee: CreatedUser,
):
    """`/questions/{别的题}/invitations/{i}` → 404，而不是「照样给你这张邀请」。

    404 而不是 403：错配的 id 不指向任何东西，403 会承认这张邀请存在。以前换成
    任意别的 question_id 都回 200，回的还是同一个 `question_id` 的那张邀请 ——
    父级 id 根本没被读过。
    """
    _, headers = author
    question_a = _create_question(api_client, headers)
    question_b = _create_question(api_client, headers)
    invitation_id = _invite(api_client, headers, question_a, invitee.user_id)

    wrong = api_client.get(
        f"/questions/{question_b}/invitations/{invitation_id}", headers=headers
    )
    assert wrong.status_code == 404, wrong.text
    # 回的是「没有」，不是「有但不给你」：body 里没有被邀请人的任何东西，也没有
    # 那个 `{"invitation": ...}` 信封。回包 `error.data` 里那个 id 是**调用者自己
    # 写在 URL 里的**，不是泄漏。
    assert "invitation" not in wrong.text
    assert invitee.nickname not in wrong.text
    assert wrong.json()["error"]["data"] == {"id": invitation_id}

    # 对照组：父级 id 对的时候，同一条路由照样回那张邀请。
    right = api_client.get(
        f"/questions/{question_a}/invitations/{invitation_id}", headers=headers
    )
    assert right.status_code == 200, right.text
    assert right.json()["data"]["invitation"]["id"] == invitation_id


def test_a_wrong_parent_and_a_missing_invitation_answer_identically(
    api_client: TestClient,
    author: tuple[CreatedUser, dict[str, str]],
    invitee: CreatedUser,
):
    """「挂在别的题上」和「压根没有这张邀请」必须是同一个答案。

    不一样就不叫 404 了：两个形状一旦分得开，它就成了一个存在性预言机 —— 顺着
    id 试过去，答得出「这个 id 有，只是不在这道题上」。所以两条路都走同一个
    `_invitation_in_question`，回的 body 逐字相同。
    """
    _, headers = author
    question_a = _create_question(api_client, headers)
    question_b = _create_question(api_client, headers)
    invitation_id = _invite(api_client, headers, question_a, invitee.user_id)

    elsewhere = api_client.get(
        f"/questions/{question_b}/invitations/{invitation_id}", headers=headers
    )
    nowhere = api_client.get(
        f"/questions/{question_b}/invitations/{invitation_id + 999999}", headers=headers
    )

    assert elsewhere.status_code == nowhere.status_code == 404
    assert elsewhere.json()["message"] == nowhere.json()["message"]
    assert elsewhere.json()["error"]["name"] == nowhere.json()["error"]["name"]
    assert (
        elsewhere.json()["error"]["data"].keys()
        == nowhere.json()["error"]["data"].keys()
    )


def test_a_wrong_parent_id_deletes_nothing(
    api_client: TestClient,
    author: tuple[CreatedUser, dict[str, str]],
    invitee: CreatedUser,
):
    """`DELETE` 那条也认父级 id：走错题的路径撤不掉邀请。

    这条紧挨着读侧一起修，因为它是同一个缺陷的另一半（`_ = question_id`）。
    以前它是**真的撤掉了**：服务只按 `invitation_id` 取行，再看那行自己的题的
    主人是不是调用者 —— URL 里写的是哪道题完全不参与，所以拿任一自己的题做前缀
    就能撤回别人题上的邀请（只要那行邀请属于的题是你的，前缀写什么都行）。

    答的码是 **400**，和「这张邀请根本不存在」那个答案**一字不差**：错配的 id 不
    指向任何东西，两个答案要是能分开，那个差别本身就是一个「这张邀请存在（在
    别处）」的探针 —— 而绑父级要关掉的正是这件事。（读侧两条路都是 404，同理。）
    """
    _, headers = author
    question_a = _create_question(api_client, headers)
    question_b = _create_question(api_client, headers)
    invitation_id = _invite(api_client, headers, question_a, invitee.user_id)

    wrong = api_client.delete(
        f"/questions/{question_b}/invitations/{invitation_id}", headers=headers
    )
    nowhere = api_client.delete(
        f"/questions/{question_b}/invitations/99999999", headers=headers
    )
    assert wrong.status_code == nowhere.status_code, (wrong.text, nowhere.text)
    assert wrong.json() == nowhere.json()

    # 邀请还在：拿原本的路径读得到，说明上一步没删掉它。
    still_there = api_client.get(
        f"/questions/{question_a}/invitations/{invitation_id}", headers=headers
    )
    assert still_there.status_code == 200, still_there.text

    # 对照组：父级 id 对的时候撤得掉。
    right = api_client.delete(
        f"/questions/{question_a}/invitations/{invitation_id}", headers=headers
    )
    assert right.status_code == 200, right.text


# --- 3. 有权的人仍然成功（口径的正面） --------------------------------------


def test_a_logged_in_stranger_reads_the_same_list(
    api_client: TestClient,
    author: tuple[CreatedUser, dict[str, str]],
    invitee: CreatedUser,
    user_client: UserCreator,
):
    """口径的正面：**任何登录用户**都读得到，这条把口径写下来，供人反驳。

    这个功能的读侧和写侧是同一个口径（`create_invitation` 只问登录），页面也是
    这么用的。所以这里断言的是 200 —— 如果产品的口径是「只有作者/被邀请人能看」，
    那么要改的不止这三条读路由，还有 `create_invitation` 和页面上的对话框；那是
    产品决定，不该顺手在一个安全修复里做掉。
    """
    _, headers = author
    stranger = user_client.create_user()
    stranger.token = user_client.login(api_client, stranger.username, stranger.password)

    question_id = _create_question(api_client, headers)
    invitation_id = _invite(api_client, headers, question_id, invitee.user_id)

    for door, path in _three_doors(question_id, invitation_id).items():
        response = api_client.get(path, headers=_headers(stranger))
        assert response.status_code == 200, f"{door}: {response.text}"


def test_the_invited_person_reads_their_own_invitation(
    api_client: TestClient,
    author: tuple[CreatedUser, dict[str, str]],
    invitee: CreatedUser,
):
    """被邀请的人读得到请自己的那张 —— 「要点头的那个」是这条路由的读者之一。"""
    _, headers = author
    question_id = _create_question(api_client, headers)
    invitation_id = _invite(api_client, headers, question_id, invitee.user_id)

    response = api_client.get(
        f"/questions/{question_id}/invitations/{invitation_id}",
        headers=_headers(invitee),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["invitation"]["user"]["id"] == invitee.user_id
