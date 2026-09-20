"""反馈中心：可见性并集、状态阶梯、以及 agent 只能提案的那条路。

这里钉的是**产品判断**，不是接口形状。四条最要紧的：

* 私密条目对第三方回 **404 而不是 403** —— 403 等于承认这条存在，而那正是提交者
  要求不要发生的事。同一条规则适用于每一个收 id 的反馈端点。
* 已解决的 **bug** 从工作栏位沉下去，已解决的 suggestion 不沉。文档里写过两遍的
  那条「窄读法」，代码必须和它一致。
* **agent 不能自己发布反馈**。它只能落一张提案卡，由人按发送；发送之后作者是卡上的
  agent，提交者是按按钮的人 —— 两个字段，所以「芝士提的反馈里有多少真的被人发出去
  了」答得出来。
* 提案的三道限流各自回 412，因为它们对客户端的指令是同一句：别重试，别再做这件事。
"""

import uuid

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.identity.handles import topic_agent_handle
from tests.integration.conftest import session_auth_headers

#: A handle the tests put in the admin allow-list. Deliberately not a real member
#: of anything: platform admin is a platform-level fact, not a project role.
ADMIN = "fb-admin"

STRANGER = "fb-stranger"
REPORTER = "fb-reporter"


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make ``ADMIN`` the platform administrator for one test.

    `admin_handles()` re-reads settings on every call precisely so this works
    without a restart (see `services.admin_handles`).
    """
    monkeypatch.setattr(settings, "feedback_admin_handles", [ADMIN])
    return ADMIN


def _project(client, handle: str) -> str:
    return client.post(
        "/projects", json={"name": "P"}, headers=session_auth_headers(handle)
    ).json()["data"]["id"]


def _topic(client, project: str, handle: str, title: str = "反馈话题") -> str:
    return client.post(
        "/topics",
        json={"project_id": project, "title": title},
        headers=session_auth_headers(handle),
    ).json()["data"]["id"]


def _report(client, handle: str, **body) -> dict:
    """File a report as ``handle`` and return the created detail."""
    payload = {"title": "按钮点了没反应", **body}
    r = client.post("/feedback", json=payload, headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _cards(client, handle: str | None = None, **params) -> list[dict]:
    headers = session_auth_headers(handle) if handle else None
    r = client.get("/feedback", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _propose(client, topic: str, token: str, **body) -> dict:
    payload = {
        "title": "沙箱里 make 装不上依赖",
        "what_happened": "make 停在 could not resolve host",
        "user_said": "用户没有就这个问题说过话，以上是芝士自己观察到的",
        **body,
    }
    return client.post(
        f"/topics/{topic}/feedback-proposals",
        json=payload,
        headers={"X-Cheese-Token": token},
    )


# --- 可见性并集 --------------------------------------------------------------


def test_private_report_is_404_for_a_stranger_and_readable_by_its_author(client):
    row = _report(client, REPORTER, visibility="private")

    # The reporter follows their own report.
    mine = client.get(f"/feedback/{row['id']}", headers=session_auth_headers(REPORTER))
    assert mine.status_code == 200, mine.text

    # Everyone else gets 404, not 403: a 403 would confirm it exists.
    theirs = client.get(
        f"/feedback/{row['id']}", headers=session_auth_headers(STRANGER)
    )
    assert theirs.status_code == 404

    # …and it is not in the public list, so the list is not a side door to it.
    assert row["id"] not in {c["id"] for c in _cards(client, STRANGER)}


def test_a_private_report_is_visible_to_an_admin(client, as_admin):
    row = _report(client, REPORTER, visibility="private")
    r = client.get(f"/feedback/{row['id']}", headers=session_auth_headers(as_admin))
    assert r.status_code == 200, r.text


def test_security_is_a_subclass_of_private(client, as_admin):
    """A public report an admin marks as a security matter stops being public.

    This is the §8.3 reading: `security` narrows visibility, it never widens it.
    An admin flag that *published* something private would be the one edit the
    reporter cannot undo, which is why the field points this way and why
    `PATCH /admin/feedback` has no `visibility` at all.
    """
    row = _report(client, REPORTER)
    assert row["visibility"] == "public"

    patched = client.patch(
        f"/admin/feedback/{row['id']}",
        json={"security": True},
        headers=session_auth_headers(as_admin),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["security"] is True

    # The reporter keeps access (they are the author), the stranger loses it.
    assert (
        client.get(
            f"/feedback/{row['id']}", headers=session_auth_headers(REPORTER)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/feedback/{row['id']}", headers=session_auth_headers(STRANGER)
        ).status_code
        == 404
    )


# --- 栏位口径 ---------------------------------------------------------------


def test_a_resolved_bug_sinks_but_a_resolved_suggestion_stays(client, as_admin):
    """§8.23 as implemented: only resolved **bugs** leave the working tabs."""
    bug = _report(client, REPORTER, title="已解决的 bug", kind="bug")
    idea = _report(client, REPORTER, title="已实现的建议", kind="suggestion")

    for row in (bug, idea):
        r = client.post(
            f"/admin/feedback/{row['id']}/status",
            json={"status": "resolved"},
            headers=session_auth_headers(as_admin),
        )
        assert r.status_code == 200, r.text

    titles = {c["title"] for c in _cards(client, tab="all")}
    assert "已实现的建议" in titles
    assert "已解决的 bug" not in titles

    # Both are in `resolved` — the tabs are filters, not a partition.
    resolved = {c["title"] for c in _cards(client, tab="resolved")}
    assert {"已解决的 bug", "已实现的建议"} <= resolved


def test_an_unknown_tab_is_refused_rather_than_silently_shown_as_all(client):
    r = client.get("/feedback", params={"tab": "hots"})
    assert r.status_code == 400


# --- 管理端 -----------------------------------------------------------------


def test_the_admin_queue_rejects_everyone_who_is_not_an_admin(client, as_admin):
    r = client.get("/admin/feedback", headers=session_auth_headers(STRANGER))
    assert r.status_code == 403

    # Anonymous is not a 403 — there is no one to refuse.
    assert client.get("/admin/feedback").status_code in (401, 403)

    ok = client.get("/admin/feedback", headers=session_auth_headers(as_admin))
    assert ok.status_code == 200, ok.text


def test_a_status_change_writes_its_timeline_entry_in_the_same_call(client, as_admin):
    row = _report(client, REPORTER)

    r = client.post(
        f"/admin/feedback/{row['id']}/status",
        json={"status": "planned"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text

    detail = r.json()["data"]
    assert detail["status"] == "planned"
    # `received` came with the report, `planned` with this call: a status that
    # moved without leaving a row would be a history that disagrees with itself.
    assert [t["status"] for t in detail["timeline"]][-2:] == [
        "received",
        "planned",
    ]


def test_setting_the_same_status_twice_is_a_no_op_not_a_second_timeline_row(
    client, as_admin
):
    row = _report(client, REPORTER)
    for _ in range(2):
        r = client.post(
            f"/admin/feedback/{row['id']}/status",
            json={"status": "triaging"},
            headers=session_auth_headers(as_admin),
        )
        assert r.status_code == 200, r.text
    assert len(r.json()["data"]["timeline"]) == 2  # received + one triaging


# --- 支持 -------------------------------------------------------------------


def test_support_is_idempotent_and_returns_the_count_after_the_write(client):
    row = _report(client, REPORTER)
    url = f"/feedback/{row['id']}/supports"
    headers = session_auth_headers(STRANGER)

    first = client.post(url, headers=headers).json()["data"]
    assert first == {"count": 1, "supported": True}

    # Pressing twice is one support: the reply is the total, not an increment,
    # so two people pressing at once cannot render a number nobody ever had.
    again = client.post(url, headers=headers).json()["data"]
    assert again == {"count": 1, "supported": True}

    assert client.delete(url, headers=headers).json()["data"] == {
        "count": 0,
        "supported": False,
    }


def test_a_resolved_bug_cannot_be_supported(client, as_admin):
    row = _report(client, REPORTER)
    client.post(
        f"/admin/feedback/{row['id']}/status",
        json={"status": "resolved"},
        headers=session_auth_headers(as_admin),
    )
    r = client.post(
        f"/feedback/{row['id']}/supports", headers=session_auth_headers(STRANGER)
    )
    # 412, not 404: the report is visible, the action is what cannot happen.
    assert r.status_code == 412


# --- 评论 -------------------------------------------------------------------


def test_a_reply_to_a_reply_lands_on_the_top_level_parent(client):
    row = _report(client, REPORTER)
    url = f"/feedback/{row['id']}/comments"

    top = client.post(
        url,
        json={"body": "我这边也能复现"},
        headers=session_auth_headers(STRANGER),
    ).json()["data"]
    assert top["parent_id"] is None

    reply = client.post(
        url,
        json={"body": "在 staging 也复现", "parent_id": top["id"]},
        headers=session_auth_headers(REPORTER),
    ).json()["data"]
    assert reply["parent_id"] == top["id"]

    # One level further down folds onto the same top-level parent rather than
    # nesting again, so the thread stays two deep however long it gets: three
    # levels is unreadable on a phone, and the prototype drew two.
    deeper = client.post(
        url,
        json={"body": "同一个现象", "parent_id": reply["id"]},
        headers=session_auth_headers(REPORTER),
    ).json()["data"]
    assert deeper["parent_id"] == top["id"]


# --- agent 通道 -------------------------------------------------------------


def test_an_agent_credential_cannot_file_a_report_on_the_direct_route(client):
    """`POST /feedback` is a person's door, and an agent token does not open it.

    Worth being precise about *which* check refuses, because the test would pass
    just as happily if the wrong one did: `/feedback` names no topic in its path,
    so a per-turn credential is out of scope and the resolver refuses it before
    the route body runs. The refusal in `create()` — the one that names
    `cheese feedback propose` — is what catches an agent on the *accept* route,
    where a topic-scoped token does resolve; see
    `test_an_agent_cannot_accept_its_own_proposal`.

    Both are 403 and both mean the same thing to the agent, so this asserts the
    outcome and not the sentence: no report exists afterwards.
    """
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)

    r = client.post(
        "/feedback",
        json={"title": "agent 想直接发一条"},
        headers={"X-Cheese-Token": token},
    )
    assert r.status_code == 403, r.text
    assert [
        c for c in _cards(client, REPORTER) if c["title"] == "agent 想直接发一条"
    ] == []


def test_proposal_endpoints_need_an_identity(client):
    """Anonymous cannot propose: with no verified caller there is nobody to
    attribute the card to, and the card's author is the whole point."""
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)

    r = client.post(
        f"/topics/{topic}/feedback-proposals",
        json={"title": "x", "user_said": "y"},
    )
    assert r.status_code == 401


def test_a_proposal_lands_as_a_card_that_a_person_then_sends(client):
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)
    agent = topic_agent_handle(topic)

    proposed = _propose(client, topic, token)
    assert proposed.status_code == 200, proposed.text
    block_id = proposed.json()["data"]["block_id"]

    live = client.get(
        f"/topics/{topic}/feedback-proposals", headers=session_auth_headers(REPORTER)
    ).json()["data"]
    assert [c["block_id"] for c in live] == [block_id]
    assert live[0]["author_handle"] == agent

    sent = client.post(
        f"/topics/{topic}/feedback-proposals/{block_id}/accept",
        json={
            "title": "沙箱里 make 装不上依赖",
            "what_happened": "make 停在 could not resolve host",
        },
        headers=session_auth_headers(REPORTER),
    )
    assert sent.status_code == 200, sent.text
    row = sent.json()["data"]

    # Two authors, not one: the agent found it, the person sent it. This pair is
    # the only thing that can answer 「芝士提的反馈里有多少真的被人发出去了」.
    assert row["author_handle"] == agent
    assert row["author_is_agent"] is True
    assert row["submitted_by_handle"] == REPORTER
    # …and it is a normal report from here on: it has a display id and a
    # `received` entry like anything a person filed by hand.
    assert row["display_id"].startswith("FB-")
    assert [t["status"] for t in row["timeline"]] == ["received"]
    assert row["topic_id"] == topic


def test_an_agent_cannot_accept_its_own_proposal(client):
    """Accepting is publishing, one step removed. Both halves are one door."""
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)

    block_id = _propose(client, topic, token).json()["data"]["block_id"]
    r = client.post(
        f"/topics/{topic}/feedback-proposals/{block_id}/accept",
        json={"title": "沙箱里 make 装不上依赖"},
        headers={"X-Cheese-Token": token},
    )
    assert r.status_code == 403, r.text
    assert "propose" in r.json()["message"], r.text


def test_the_same_proposal_is_refused_the_second_time(client):
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)

    assert _propose(client, topic, token).status_code == 200
    # Same words, reflowed: the fingerprint normalises punctuation, whitespace
    # and case, so this is the same proposal and gets refused rather than
    # stacking up as a second card.
    again = _propose(
        client,
        topic,
        token,
        what_happened=" MAKE 停在   Could.Not   Resolve Host ",
    )
    assert again.status_code == 412


def test_dismissing_a_proposal_is_remembered(client):
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)

    block_id = _propose(client, topic, token).json()["data"]["block_id"]
    r = client.post(
        f"/topics/{topic}/feedback-proposals/{block_id}/dismiss",
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text

    # Out of the chat column…
    live = client.get(
        f"/topics/{topic}/feedback-proposals", headers=session_auth_headers(REPORTER)
    ).json()["data"]
    assert live == []

    # …and it stays out: the refusal is on the server, so a reload does not bring
    # the card back and a re-proposal is refused rather than re-asked.
    assert _propose(client, topic, token).status_code == 412


def test_the_topic_runs_out_of_proposals_for_the_day(client):
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)

    for n in range(settings.feedback_proposals_per_topic_per_day):
        r = _propose(client, topic, token, what_happened=f"第 {n} 个不同的问题")
        assert r.status_code == 200, r.text

    over = _propose(client, topic, token, what_happened="第 N+1 个不同的问题")
    assert over.status_code == 412


# --- 未读游标 ---------------------------------------------------------------


def test_the_unread_cursor_counts_activity_and_clears_when_read(client):
    row = _report(client, REPORTER, title="游标测试")
    headers = session_auth_headers(REPORTER)

    client.post("/feedback/read", headers=headers)
    before = client.get("/feedback/counts", headers=headers).json()["data"]["unread"]

    # Someone else comments on it: activity the reporter has not read.
    client.post(
        f"/feedback/{row['id']}/comments",
        json={"body": "我也遇到了"},
        headers=session_auth_headers(STRANGER),
    )
    after = client.get("/feedback/counts", headers=headers).json()["data"]["unread"]
    assert after > before

    client.post("/feedback/read", headers=headers)
    cleared = client.get("/feedback/counts", headers=headers).json()["data"]["unread"]
    assert cleared < after


def test_meta_reports_the_vocabulary_and_my_admin_flag(client, as_admin):
    anon = client.get("/feedback/meta").json()["data"]
    assert anon["is_admin"] is False
    assert "resolved" in anon["status_ladder"]
    assert anon["hot_supports"] == 5

    mine = client.get("/feedback/meta", headers=session_auth_headers(as_admin)).json()[
        "data"
    ]
    assert mine["is_admin"] is True


def test_a_report_can_point_at_the_topic_it_came_from(client):
    """`topic_id` is a plain reference, not a foreign key the report depends on.

    A report outlives the conversation that produced it — that is the whole
    reason it is filed in a platform-level inbox and not under the topic.
    """
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    row = _report(client, REPORTER, topic_id=topic, project_id=project)
    assert row["topic_id"] == topic
    assert uuid.UUID(row["id"])
