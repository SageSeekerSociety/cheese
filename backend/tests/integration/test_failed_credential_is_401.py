"""一个验不过的 bearer，要当场说 401，而不是降级成匿名再回 200。

这是 #323 的后一半。#323 修的是泄漏：token 失效后，`GET /projects` 曾把平台上
每个人的项目都塞进侧栏（dev 上实测，同一秒内有效 token 回 1 个、`Bearer
not.a.jwt` 回 12 个，含四个别人的）。修法是「认不出人就回空」——安全那半对了，
但对**当事人**来说答案还是假的：他明明递了凭据，得到的却是一个跟「你还没登录」
一模一样的 200 空列表，前端没有任何理由去换 token。

2026-08-12 在 dev 上量到的形状就是这个：同一个坏 token，`topic-unread` 回 401、
`/projects` 回 `200 n=0`。于是自愈只能等下一次未读轮询（30 秒一次）撞上那条会
401 的路由，侧栏在这期间一直空着。

这批测试钉的是那条线本身——**递了但验不过** ≠ **什么都没递**：
"""

from tests.integration.conftest import session_auth_headers, session_token


def _projects(client, headers: dict | None = None):
    return client.get("/projects", headers=headers or {})


def test_a_bearer_that_does_not_verify_is_rejected_not_emptied(client):
    """线上那个形状：签名过不了。回 401，前端才会去换凭据。"""
    r = _projects(client, {"Authorization": "Bearer not.a.jwt"})

    assert r.status_code == 401, r.text
    assert "登录" in r.json()["message"]


def test_a_forged_signature_is_rejected_too(client):
    """结构完全合法、只有签名是假的——比 `not.a.jwt` 更接近真实故障（后端换了
    签名密钥后，旧 token 就长这样）。"""
    good = session_token("alice")
    forged = good[: good.rindex(".")] + ".AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    assert _projects(client, {"Authorization": f"Bearer {forged}"}).status_code == 401


def test_presenting_nothing_at_all_still_answers_an_empty_list(client):
    """没递凭据的人不是「凭据坏了」，是「还没登录」——这条路由对他们仍然是 200
    空列表，不是 401。把这两者合并成 401 会让未登录的落地页直接报错。"""
    r = _projects(client)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 0


def test_a_good_token_still_gets_its_own_projects(client):
    """正常路径不能被这条规则碰到。"""
    made = client.post(
        "/projects", json={"name": "P"}, headers=session_auth_headers("alice")
    )
    assert made.status_code == 200, made.text

    r = _projects(client, session_auth_headers("alice"))

    assert r.status_code == 200, r.text
    assert [p["name"] for p in r.json()["data"]["data"]] == ["P"]


def test_the_team_scoped_listing_answers_401_too(client):
    """带 `team_id` 的那条分支现在也在这条规则里。

    它曾经根本不解析调用者——理由是「它答的是这个团队的项目，不是我的项目」。那个
    理由在 2026-09-09 作废了：那份列表每一行都带着项目的 `id`，而 id 就是这个项目
    的名册、决策记录和用量的钥匙，所以它答的从来就不只是团队的事。现在它要求调用
    者是这个团队的成员，于是「递了但验不过」在这条分支上也是 401——和这个文件里
    其余几条同一个理由，不是新规矩。"""
    r = client.get("/projects?team_id=1", headers={"Authorization": "Bearer bad"})

    assert r.status_code == 401, r.text
