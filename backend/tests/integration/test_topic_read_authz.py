"""读话题也要鉴权：`GET /topics/{id}` 和 `/blocks` 曾经完全不查。

同一个文件里 `/comments`、`/doc` 都调了 `resolver.authorize_topic`，只有这两条没
调——所以这是漏了，不是策略。而漏掉的偏偏是**承载对话本身**的那条：

    --- mallory（登录了，但和这个项目毫无关系）---
      GET /topics/{id}        -> 200, title='薪资讨论'
      GET /topics/{id}/blocks -> 200, contents=['机密：下季度裁员名单']

上面是改动前在测试库上**实测**的输出，不是推断。

边界写清楚，免得这个文件看起来比实际管得多：`authorize_topic_access`
（`app/domain/authz/policy.py:60`）第一行就是
`if not actor.authenticated or actor.is_agent: return True`，所以接上鉴权只关掉
「已登录的局外人」这一半。**匿名调用者仍然读得到**——那属于 policy 里写明的
「阶段三关入口」，跟 `refuse_unauthenticated_chat` 是同一个动作，不在这次范围内。
下面 `test_an_anonymous_caller_is_still_let_through` 就是把这个边界钉死，它绿不是
「安全了」，是「这一半还没关，谁改动了要先看见」。

也正因为第一行那个 `or actor.is_agent`，接上鉴权不可能锁死分身或前端：能被**新
拒绝**的只有「已认证且不是分身且不在名册里」的人。
"""

from tests.integration.conftest import session_auth_headers


def _project_with_a_secret(client) -> tuple[str, str]:
    """A project owned by alice, whose root topic holds one sensitive line."""
    p = client.post("/projects", json={"name": "薪资", "owner_handle": "alice"}).json()[
        "data"
    ]
    tid = p["root_topic_id"]
    r = client.post(
        f"/topics/{tid}/decision",
        json={"decision": "机密：下季度裁员名单"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return p["id"], tid


def test_an_outsider_cannot_read_the_conversation(client):
    """The one that actually leaked. `/blocks` carries the messages verbatim."""
    _, tid = _project_with_a_secret(client)
    r = client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("mallory"))
    assert r.status_code == 403, r.text


def test_an_outsider_cannot_read_the_topic_header(client):
    """The title alone is worth denying — 「薪资讨论」 tells you plenty."""
    _, tid = _project_with_a_secret(client)
    r = client.get(f"/topics/{tid}", headers=session_auth_headers("mallory"))
    assert r.status_code == 403, r.text


def test_a_member_still_reads_both(client):
    """The half that must NOT change. A guard that also locks out the owner is
    not a fix, it is an outage."""
    _, tid = _project_with_a_secret(client)
    head = client.get(f"/topics/{tid}", headers=session_auth_headers("alice"))
    assert head.status_code == 200, head.text

    body = client.get(f"/topics/{tid}/blocks", headers=session_auth_headers("alice"))
    assert body.status_code == 200, body.text
    assert any("裁员名单" in (b["content"] or "") for b in body.json()["data"]["data"])


def test_an_anonymous_caller_is_still_let_through(client):
    """The documented boundary, pinned deliberately — see this module's docstring.

    `authorize_topic_access` returns True for an unauthenticated actor, so the
    no-credential path is untouched by this change. Every existing caller that
    reads these routes without a token (the whole rest of the suite, and agents
    mid-turn) therefore keeps working. When 阶段三 closes the entrance, THIS test
    is the one that flips — which is the point of writing it down.
    """
    _, tid = _project_with_a_secret(client)
    assert client.get(f"/topics/{tid}").status_code == 200
    assert client.get(f"/topics/{tid}/blocks").status_code == 200
