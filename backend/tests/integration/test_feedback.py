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
from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.device_hub import HubScreen, device_hub
from app.domain.feedback.models import Feedback
from app.domain.identity.handles import looks_like_agent_handle
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


def _mine(client, handle: str) -> list[dict]:
    r = client.get("/feedback/mine", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _register_screen(*, project_id=None, topic_id=None, handle: str) -> HubScreen:
    """A device screen registered straight on the singleton hub.

    Copied from `test_connector_viewer.py`: no live device is needed — the
    resolver attributes a call carrying this token to its agent-user, and that is
    the only thing these tests want from it.
    """
    screen = HubScreen(
        sid="s" + uuid.uuid4().hex[:8],
        device_id="d" + uuid.uuid4().hex[:6],
        command=["claude"],
        token=uuid.uuid4().hex,
        agent_user_id=uuid.uuid4(),
        agent_handle=handle,
        project_id=project_id,
        topic_id=topic_id,
    )
    device_hub._screens[screen.sid] = screen
    device_hub._by_screen_token[screen.token] = screen
    device_hub._device(screen.device_id).screens[screen.sid] = screen
    return screen


def _unregister(screen: HubScreen) -> None:
    device_hub._screens.pop(screen.sid, None)
    device_hub._by_screen_token.pop(screen.token, None)
    device_hub._devices.pop(screen.device_id, None)


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

    proposed = _propose(client, topic, token)
    assert proposed.status_code == 200, proposed.text
    block_id = proposed.json()["data"]["block_id"]

    live = client.get(
        f"/topics/{topic}/feedback-proposals", headers=session_auth_headers(REPORTER)
    ).json()["data"]
    assert [c["block_id"] for c in live] == [block_id]
    # Which agent is credited comes off the card, not off a derivation: an
    # identity belongs to the agent (`agent_instance_handle`), and a room no
    # longer names one — a test that recomputes the handle would be testing the
    # naming rule rather than this route. What matters here is that the author is
    # an agent and is not the person who filed it.
    agent = live[0]["author_handle"]
    assert looks_like_agent_handle(agent)
    assert agent != REPORTER

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
    """`topic_id` says which room it came from, and does not depend on it.

    A real foreign key, nullable and `ON DELETE SET NULL` (`models.py`): a
    report outlives the conversation that produced it — that is the whole reason
    it is filed in a platform-level inbox and not under the topic.
    """
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    row = _report(client, REPORTER, topic_id=topic, project_id=project)
    assert row["topic_id"] == topic
    assert uuid.UUID(row["id"])


# --- 「我的反馈」和详情必须给同一个答案 ---------------------------------------


def test_the_mine_list_offers_exactly_what_the_detail_route_will_open(client, as_admin):
    """「我的反馈」 是一份**能打开的**清单：里面有的都能开，能开的都在里面。

    Being *assigned* a report is not access to it. §8.9 gives the assignee no
    management power, and §4.3's visibility union is 提交者 ∪ 管理员. The list used
    to include the assignee arm unfiltered, so a third party was handed the title
    and status of a private report on one endpoint while the detail endpoint
    answered 404 for it — one rule, two answers, depending on which one you asked.
    A list that disagrees with its own rows is worse than either answer alone,
    because the client has no way to tell which one lied.

    Written as an agreement over a table of rows rather than as one case: every
    row below is checked with the same two questions, so a future edit that widens
    or narrows either side lands here.
    """
    mine_private = _report(client, STRANGER, title="我提的私密", visibility="private")
    mine_public = _report(client, STRANGER, title="我提的公开")
    theirs_private = _report(
        client, REPORTER, title="别人提的私密", visibility="private"
    )
    theirs_public = _report(client, REPORTER, title="别人提的公开")
    theirs_security = _report(client, REPORTER, title="别人提的安全")

    for row in (theirs_private, theirs_public, theirs_security):
        patched = client.patch(
            f"/admin/feedback/{row['id']}",
            json={"assignee_handle": STRANGER},
            headers=session_auth_headers(as_admin),
        )
        assert patched.status_code == 200, patched.text
    # `security` narrows visibility, it never widens it (§8.3) — so this row is
    # the one an admin flagged, and the assignee is still nobody.
    flagged = client.patch(
        f"/admin/feedback/{theirs_security['id']}",
        json={"security": True},
        headers=session_auth_headers(as_admin),
    )
    assert flagged.status_code == 200, flagged.text

    listed = {card["id"] for card in _mine(client, STRANGER)}

    def openable(row: dict) -> bool:
        return (
            client.get(
                f"/feedback/{row['id']}", headers=session_auth_headers(STRANGER)
            ).status_code
            == 200
        )

    for row in (
        mine_private,
        mine_public,
        theirs_private,
        theirs_public,
        theirs_security,
    ):
        assert (row["id"] in listed) == openable(row), row["title"]

    # The third arm — 我提的和 agent 替我提的 — is checked from the other side:
    # the reporter did not write this row (the agent did), and it is still theirs,
    # private and all.
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)
    block_id = _propose(client, topic, token).json()["data"]["block_id"]
    sent = client.post(
        f"/topics/{topic}/feedback-proposals/{block_id}/accept",
        json={"title": "agent 替我提的", "visibility": "private"},
        headers=session_auth_headers(REPORTER),
    ).json()["data"]
    assert sent["author_handle"] != REPORTER
    assert sent["id"] in {card["id"] for card in _mine(client, REPORTER)}
    assert (
        client.get(
            f"/feedback/{sent['id']}", headers=session_auth_headers(REPORTER)
        ).status_code
        == 200
    )


def test_an_admin_sees_everything_assigned_to_them_in_their_own_list(client, as_admin):
    """The narrowing above is for people who are not admins.

    An admin is never narrowed (`may_see`'s second arm), so an admin assigned a
    private report keeps it in 「我的反馈」 as well as in the 分诊台. Pinned because
    the obvious way to write the fix — AND `PUBLIC_ONLY` onto the assignee arm for
    everyone — would have taken it away from them, and nothing else would say so.
    """
    row = _report(client, REPORTER, title="指派给管理员的私密", visibility="private")
    client.patch(
        f"/admin/feedback/{row['id']}",
        json={"assignee_handle": as_admin},
        headers=session_auth_headers(as_admin),
    )
    assert row["id"] in {card["id"] for card in _mine(client, as_admin)}


# --- 管理端的那个数字 -------------------------------------------------------


def test_unassigned_is_the_admin_queues_number_and_appears_nowhere_else(
    client, as_admin
):
    """「还没人管」 is counted over every unresolved row — private and security too —
    so it is an admin read, and the public endpoints are where it must not be.

    `/feedback/counts` answers anonymous callers on purpose (the bell polls it
    before anyone signs in), which is what made this one worth a test: the number
    was riding along on a response that needs no identity at all.
    """
    _report(client, REPORTER, title="没人认领的私密", visibility="private")
    _report(client, REPORTER, title="没人认领的公开")

    assert "unassigned" not in client.get("/feedback/counts").json()["data"]
    # …nor on the list, which carries the same counts object.
    listed = client.get("/feedback").json()["data"]["counts"]
    assert "unassigned" not in listed

    # Not vacuous: the admin, who may open those rows, does get the number.
    admin = client.get(
        "/admin/feedback", headers=session_auth_headers(as_admin)
    ).json()["data"]["counts"]
    assert admin["unassigned"] >= 2


def test_the_hot_tab_counts_the_rows_it_shows(client, as_admin):
    """The number on a tab and the rows behind it are one query's answer.

    `hot` means 「支持数 ≥ 5」, and it does not sink resolved *suggestions* — only
    resolved bugs sink (§8.23). The count used a bare `status != resolved`, so a
    resolved suggestion was in the list and not in the number, and the tab read
    「4」 over five cards. One predicate, or the two drift.
    """
    idea = _report(client, REPORTER, title="已实现的建议", kind="suggestion")
    # The threshold the UI reads, not a 5 typed twice.
    hot_supports = client.get("/feedback/meta").json()["data"]["hot_supports"]
    for n in range(hot_supports):
        r = client.post(
            f"/feedback/{idea['id']}/supports",
            headers=session_auth_headers(f"fb-supporter-{n}"),
        )
        assert r.status_code == 200, r.text

    client.post(
        f"/admin/feedback/{idea['id']}/status",
        json={"status": "resolved"},
        headers=session_auth_headers(as_admin),
    )

    page = client.get("/feedback", params={"tab": "hot"}).json()["data"]
    assert idea["id"] in {card["id"] for card in page["data"]}
    assert page["counts"]["hot"] == len(page["data"])


def test_rows_that_tie_on_the_sort_key_still_come_back_in_one_order(
    client, db_session: AsyncSession, _portal: BlockingPortal
):
    """An ordering two rows can tie on is not an ordering.

    `sort=supports` ordered by `count(supports) DESC, created_at DESC` and stopped
    there. `created_at` comes from the application clock, so two rows made in the
    same millisecond compare equal, and OFFSET paging over a tie can repeat or
    skip a row between two requests. The `new` branch already broke the tie on
    `display_no`; this branch did not.

    The two rows are given the same `created_at` and no supports at all, which is
    that tie exactly. `display_no` decides it: 后建的 first, the same direction the
    `new` branch sorts.

    Which of two tied rows comes back first is the planner's choice, so this test
    can pass against an ordering that has no tiebreak at all: it pins the intended
    direction, and is not by itself proof that the tiebreak is there. That proof
    is `tests/unit/test_feedback_sort_ordering.py`, which reads the `ORDER BY`.
    """
    older = _report(client, REPORTER, title="先建的")
    newer = _report(client, REPORTER, title="后建的")

    async def _pin_both_to_one_instant() -> None:
        await db_session.execute(
            update(Feedback).values(created_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        await db_session.flush()

    _portal.call(_pin_both_to_one_instant)

    def page(start: int) -> list[dict]:
        r = client.get(
            "/feedback",
            params={"sort": "supports", "page_size": 1, "page_start": start},
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]["data"]

    assert [card["id"] for card in page(0)] == [newer["id"]]
    # …and paging sees each of them once, in the one order it just promised.
    assert [card["id"] for card in page(1)] == [older["id"]]


# --- agent 与提案卡 ---------------------------------------------------------


def test_an_agent_handle_on_the_admin_list_is_still_refused(client, monkeypatch):
    """§4.3's second gate, and why it has to be in the route body.

    `/admin/*` is not in `_CHEESE_WRITE_PATHS`, and that table is a whitelist —
    nothing in the middleware looks at this prefix, so the refusal has to be
    written here or it does not exist. The handle below is **on the platform
    admin list**, so the allow-list is not what refuses it; the question being
    asked is 「是不是人」.

    A device screen's token is the reachable way in: it resolves to its
    agent-as-user on any path, unlike a per-turn `cheese` credential, which the
    resolver refuses when there is no project to scope it to. The same handle
    asked as a *person* is the control — it is allowed through, which is what says
    the refusal is about being an agent.
    """
    agent = "agent-on-the-list"
    monkeypatch.setattr(settings, "feedback_admin_handles", [agent])
    screen = _register_screen(handle=agent)
    try:
        allowed = client.get("/admin/feedback", headers=session_auth_headers(agent))
        assert allowed.status_code == 200, allowed.text

        refused = client.get(
            "/admin/feedback", headers={"X-Cheese-Screen": screen.token}
        )
        assert refused.status_code == 403, refused.text
        assert "agent" in refused.json()["message"]

        # A write route too: every handler in the module goes through one helper,
        # and this is what says the helper is actually on them.
        wrote = client.post(
            f"/admin/feedback/{_report(client, REPORTER)['id']}/status",
            json={"status": "triaging"},
            headers={"X-Cheese-Screen": screen.token},
        )
        assert wrote.status_code == 403, wrote.text
    finally:
        _unregister(screen)


def test_sending_the_same_card_twice_files_one_report(client):
    """Sending leaves a record on the card, so the send happens once.

    Before this the card came back on the next load — the send was invisible to
    `live_cards` — and pressing 「提交反馈」 again filed a second report identical to
    the first, with the same title, by the same submitter, naming the same agent.
    """
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)
    block_id = _propose(client, topic, token).json()["data"]["block_id"]
    url = f"/topics/{topic}/feedback-proposals/{block_id}/accept"
    body = {
        "title": "沙箱里 make 装不上依赖",
        "what_happened": "make 停在 could not resolve host",
    }

    def send_card():
        return client.post(
            url, json=body, headers=session_auth_headers(REPORTER)
        ).json()["data"]

    first = send_card()
    again = send_card()

    # The same report, not a twin of it.
    assert again["id"] == first["id"]
    assert again["display_id"] == first["display_id"]

    # …and the card is out of the chat column, so the button is not offered again
    # on the next load either.
    live = client.get(
        f"/topics/{topic}/feedback-proposals", headers=session_auth_headers(REPORTER)
    ).json()["data"]
    assert live == []


def test_two_proposals_that_leave_the_body_blank_are_two_proposals(client):
    """Two optional fields, both blank, and still two problems.

    「发生了什么」 and 「怎么复现」 are both optional on `FeedbackProposalIn`.

    Hashing only those two made every such proposal share the fingerprint of the
    empty string, so the second one in a topic was refused with 「这个提案刚提过」 —
    a claim about its content that the server could not make.
    """
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)

    first = _propose(client, topic, token, title="第一件事", what_happened=None)
    second = _propose(client, topic, token, title="第二件事", what_happened=None)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    # Two cards, both live: the dedup did not eat one of them.
    live = client.get(
        f"/topics/{topic}/feedback-proposals", headers=session_auth_headers(REPORTER)
    ).json()["data"]
    assert len(live) == 2
