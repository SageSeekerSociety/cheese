"""反馈中心：可见性并集、状态阶梯、以及 agent 只能提案的那条路。

这里钉的是**产品判断**，不是接口形状。四条最要紧的：

* 私密条目对第三方回 **404 而不是 403** —— 403 等于承认这条存在，而那正是提交者
  要求不要发生的事。同一条规则适用于每一个收 id 的反馈端点。
* 办完了的 **bug** 从工作栏位沉下去，办完了的 suggestion 不沉。文档里写过两遍的
  那条「窄读法」，代码必须和它一致。
* **agent 不能自己发布反馈**。它只能落一张提案卡，由人按发送；发送之后作者是卡上的
  agent，提交者是按按钮的人 —— 两个字段，所以「芝士提的反馈里有多少真的被人发出去
  了」答得出来。
* 提案的三道限流各自回 412，因为它们对客户端的指令是同一句：别重试，别再做这件事。
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.device_hub import HubScreen, device_hub
from app.domain.feedback import repositories as feedback_repo
from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackKind,
    FeedbackVisibility,
)
from app.domain.feedback.repositories import FeedbackRepository
from app.domain.identity.handles import agent_instance_handle, looks_like_agent_handle
from tests.integration.conftest import room_agent_seat, session_auth_headers

#: A handle the tests put in the admin allow-list. Deliberately not a real member
#: of anything: platform admin is a platform-level fact, not a project role.
ADMIN = "fb-admin"

STRANGER = "fb-stranger"
REPORTER = "fb-reporter"

#: 竞态用例把删除的锁拿满这么久才提交，窗口就是这么撑开的。长到「排队等锁」
#: （约等于这一整段）和「根本没排」（毫秒）之间差三个数量级，短到整个用例还能忍受。
_HOLD_SECONDS = 2.0

#: 让删除先拿到锁再插回复。不等一下的话，这条用例测的可能是「谁先跑」，
#: 而不是「两者冲不冲突」。
_HEAD_START_SECONDS = 0.3


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make ``ADMIN`` the platform administrator for one test.

    `admin_handles()` re-reads settings on every call precisely so this works
    without a restart (see `services.admin_handles`).
    """
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
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
    """§8.23 as implemented: only finished **bugs** leave the working tabs.

    Both finished rungs, in one test on purpose: `resolved` and `deployed` have to
    behave alike here or 「部署」 becomes a status a bug can sit in visibly forever
    — the exact thing the sink rule exists to prevent. Same for the `resolved`
    tab below: to the person who filed it, 解决 and 部署 are two halves of one
    answer, so a deployed row that vanished from that tab reads as missing.
    """
    rows = {
        "已修复的 bug": ("bug", "resolved"),
        "已上线的 bug": ("bug", "deployed"),
        "已修复的建议": ("suggestion", "resolved"),
        "已上线的建议": ("suggestion", "deployed"),
    }
    for title, (kind, status) in rows.items():
        row = _report(client, REPORTER, title=title, kind=kind)
        r = client.post(
            f"/admin/feedback/{row['id']}/status",
            json={"status": status},
            headers=session_auth_headers(as_admin),
        )
        assert r.status_code == 200, r.text

    titles = {c["title"] for c in _cards(client, tab="all")}
    assert {"已修复的建议", "已上线的建议"} <= titles
    assert not {"已修复的 bug", "已上线的 bug"} & titles

    # All four are in `resolved` — the tabs are filters, not a partition, and
    # `resolved` is the closed set rather than the status of the same name.
    resolved = {c["title"] for c in _cards(client, tab="resolved")}
    assert set(rows) <= resolved


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
        json={"status": "resolved"},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text

    detail = r.json()["data"]
    assert detail["status"] == "resolved"
    # `received` came with the report, `resolved` with this call: a status that
    # moved without leaving a row would be a history that disagrees with itself.
    assert [t["status"] for t in detail["timeline"]][-2:] == [
        "received",
        "resolved",
    ]


def test_a_retired_status_is_rejected_rather_than_stored(client, as_admin):
    """`triaging` / `planned` were rungs until the ladder became four.

    The column is a `VARCHAR(16)` with no database-side enum (`_enum`), so a
    retired value would be *stored* happily and only blow up later, on the read
    that tries to parse it back — a row that 500s whichever page renders it, and
    by then the writer is long gone. The schema is the only place that can say
    no, so this is the test that says it does.
    """
    row = _report(client, REPORTER)

    for retired in ("triaging", "planned"):
        # 400, not 422: the app maps every `RequestValidationError` to the
        # platform's `{code, message, data}` envelope (`core/errors.py`), so the
        # status code here is the app's convention rather than FastAPI's default.
        r = client.post(
            f"/admin/feedback/{row['id']}/status",
            json={"status": retired},
            headers=session_auth_headers(as_admin),
        )
        assert r.status_code == 400, r.text

    detail = client.get(f"/feedback/{row['id']}").json()["data"]
    assert detail["status"] == "received"


def test_setting_the_same_status_twice_is_a_no_op_not_a_second_timeline_row(
    client, as_admin
):
    row = _report(client, REPORTER)
    for _ in range(2):
        r = client.post(
            f"/admin/feedback/{row['id']}/status",
            json={"status": "in_progress"},
            headers=session_auth_headers(as_admin),
        )
        assert r.status_code == 200, r.text
    assert len(r.json()["data"]["timeline"]) == 2  # received + one in_progress


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


def test_a_finished_report_cannot_be_supported(client, as_admin):
    """Both closed rungs, not just `resolved`: supporting a shipped thing is a
    vote for work that is already done, and leaving `deployed` out would make
    「部署」 a status you can pile support onto."""
    for status in ("resolved", "deployed"):
        row = _report(client, REPORTER)
        client.post(
            f"/admin/feedback/{row['id']}/status",
            json={"status": status},
            headers=session_auth_headers(as_admin),
        )
        r = client.post(
            f"/feedback/{row['id']}/supports", headers=session_auth_headers(STRANGER)
        )
        # 412, not 404: the report is visible, the action is what cannot happen.
        assert r.status_code == 412, r.text


# --- 评论 -------------------------------------------------------------------


def _comment(
    client, handle: str, feedback_id: str, body: str, *, parent_id=None
) -> dict:
    payload: dict = {"body": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    r = client.post(
        f"/feedback/{feedback_id}/comments",
        json=payload,
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _thread(client, handle: str, feedback_id: str) -> list[dict]:
    """一页评论的 **items**。评论分页之后响应是个信封（`items` + `next_cursor`），
    而这些用例问的是「楼里有什么」，不是「还有没有下一页」—— 那条另有专门的用例。"""
    r = client.get(
        f"/feedback/{feedback_id}/comments", headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["items"]


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


def test_a_reply_names_its_target_exactly_when_the_fold_lost_it(client):
    """`reply_to_handle` 只在**被回复的那条本身也是回复**时才有值。

    三行合起来是这条规则的全部：顶层评论没有回复对象；回楼主的回复紧挨着楼主渲染，
    再挂一个「回复 楼主」是每一条楼中楼都有的噪声；回楼中楼的回复被折到了同一个
    父级下，**折叠动作本身把指向弄丢了** —— 名字必须在写的时候记下来，读的时候谁也
    猜不出来。B站和小红书画这个前缀，画的正好是第三种。
    """
    row = _report(client, REPORTER)
    fid = row["id"]

    top = _comment(client, STRANGER, fid, "我这边也能复现")
    assert top["reply_to_handle"] is None

    to_top = _comment(client, REPORTER, fid, "在 staging 也复现", parent_id=top["id"])
    assert to_top["reply_to_handle"] is None

    to_reply = _comment(client, ADMIN, fid, "同一个现象", parent_id=to_top["id"])
    assert to_reply["reply_to_handle"] == REPORTER

    # 发出去的那一条和读回来的那一条必须说同一句话：两个地方各建一次 payload
    # 就会漂开，而漂开的表现是「刚发完看着对、刷新一下前缀没了」。
    #
    # 只比**和读者无关**的那几个字段。上面三条是各自作者的眼睛里看到的，而这一份是
    # STRANGER 读的，`can_delete` 两边本来就该不一样 —— 作者刚写完的东西自己删得掉，
    # 路过的人删不掉。那条规则归另一个用例管，混进来只会让这条用例说不出它在说什么。
    read_back = {c["id"]: c for c in _thread(client, STRANGER, fid)}
    assert set(read_back) == {top["id"], to_top["id"], to_reply["id"]}
    for posted in (top, to_top, to_reply):
        for field in (
            "parent_id",
            "author_handle",
            "body",
            "reply_to_handle",
            "created_at",
        ):
            assert read_back[posted["id"]][field] == posted[field]


def test_a_reply_cannot_be_hung_on_a_comment_of_another_report(client):
    mine = _report(client, REPORTER)
    other = _report(client, REPORTER)
    foreign = _comment(client, REPORTER, other["id"], "另一条反馈下面的话")

    r = client.post(
        f"/feedback/{mine['id']}/comments",
        json={"body": "挂错了", "parent_id": foreign["id"]},
        headers=session_auth_headers(REPORTER),
    )
    # 400, not 404: 到这个份上调用方已经证明了自己看得见这条反馈，出问题的是它递过来
    # 的那个 id（和它自己那张单子对不上），不是它不该知道的东西。
    assert r.status_code == 400, r.text
    assert _thread(client, REPORTER, mine["id"]) == []


# --- 删自己的评论 -------------------------------------------------------------


def test_a_comment_is_deleted_by_its_author_or_an_admin(client, as_admin):
    row = _report(client, REPORTER)
    fid = row["id"]
    url = f"/feedback/{fid}/comments"

    mine = _comment(client, REPORTER, fid, "我提的，我删得掉")
    theirs = _comment(client, STRANGER, fid, "别人提的")
    admins = _comment(client, STRANGER, fid, "管理员删得掉")

    # 非作者、非管理员：403 而不是 404。到这一步对方已经看得见这条反馈了，藏一个
    # 它明明看得见的评论没有意义 —— 缺的是权限，就得说权限。
    r = client.delete(f"{url}/{theirs['id']}", headers=session_auth_headers(REPORTER))
    assert r.status_code == 403, r.text

    r = client.delete(f"{url}/{mine['id']}", headers=session_auth_headers(REPORTER))
    assert r.status_code == 200, r.text

    r = client.delete(f"{url}/{admins['id']}", headers=session_auth_headers(as_admin))
    assert r.status_code == 200, r.text

    # 删掉的是「这条话」，不是「这层楼」：列表里查无此条，但没人能问出它曾经在。
    assert _thread(client, STRANGER, fid) == [theirs]
    # 再删一次是 404 —— 它已经不在了，而不是「你刚删过了」。
    assert (
        client.delete(
            f"{url}/{mine['id']}", headers=session_auth_headers(REPORTER)
        ).status_code
        == 404
    )


def test_deleting_a_comment_takes_its_replies_with_it(client):
    """删顶层评论，楼里的回复跟着走。

    这不是收拾整洁。客户端渲染一栋楼的顺序是「先取没有 parent_id 的那些，再逐个问
    它们的回复」，而列表把已删的行滤掉了 —— 所以一栋楼的头没了，底下的回复不是
    掉一行，是**整栋楼从屏幕上消失**，没有墓碑、也没有任何东西会再去读它们。
    B站和小红书删评论是同一个结果，而另一个读法根本站不住：一条回复写着「回复 X」、
    而 X 已经不在页面上，比两种答案都糟。
    """
    row = _report(client, REPORTER)
    fid = row["id"]

    kept = _comment(client, STRANGER, fid, "这栋楼留着")
    top = _comment(client, STRANGER, fid, "这栋楼要删")
    first = _comment(client, REPORTER, fid, "在 staging 也复现", parent_id=top["id"])
    second = _comment(client, REPORTER, fid, "同一个现象", parent_id=first["id"])

    r = client.delete(
        f"/feedback/{fid}/comments/{top['id']}",
        headers=session_auth_headers(STRANGER),
    )
    assert r.status_code == 200, r.text

    left = _thread(client, STRANGER, fid)
    assert [c["id"] for c in left] == [kept["id"]]
    # 两条回复都不在，而且不是「还在列表里但没人挂得住」—— 那种孤儿正是这条用例
    # 之前的形状：`parent_id` 指向一个查不到的父亲，谁也不会再问起它们。
    assert {second["id"], first["id"]}.isdisjoint({c["id"] for c in left})


def test_a_comment_on_a_report_you_cannot_see_is_not_a_comment_at_all(client):
    """私密反馈底下的评论，对第三方回 404 —— 每一个收 id 的端点都走同一条路。

    先判反馈再判评论，这个顺序是有意的：反过来的话，「这条评论不属于这条反馈」和
    「这条反馈你看不见」会回出两种不同的东西，而后者正是把「存在」泄露出去的那一种。
    """
    private = _report(client, REPORTER, visibility="private")
    stranger = session_auth_headers(STRANGER)

    r = client.post(
        f"/feedback/{private['id']}/comments",
        json={"body": "偷看"},
        headers=stranger,
    )
    assert r.status_code == 404, r.text

    # 作者自己看得见，所以这里先落一条真评论，再拿它的 id 让第三方去点。
    hidden = _comment(client, REPORTER, private["id"], "只有我和管理员看得到")
    base = f"/feedback/{private['id']}/comments/{hidden['id']}"

    for method in (client.delete,):
        assert method(base, headers=stranger).status_code == 404
    assert client.post(f"{base}/likes", headers=stranger).status_code == 404
    assert client.delete(f"{base}/likes", headers=stranger).status_code == 404


# --- 评论点赞 -----------------------------------------------------------------


def test_a_like_is_one_per_person_and_the_reply_is_the_total(client):
    row = _report(client, REPORTER)
    comment = _comment(client, STRANGER, row["id"], "说得对")
    url = f"/feedback/{row['id']}/comments/{comment['id']}/likes"
    headers = session_auth_headers(REPORTER)

    assert client.post(url, headers=headers).json()["data"] == {
        "count": 1,
        "liked": True,
    }
    # 和 `supports` 同一句：回的是**写完之后的总数**，不是增量。两个人同时点，各
    # 自渲染出一个自己加一的结果，页面上就会出现一个从来没存在过的数字。
    assert client.post(url, headers=headers).json()["data"] == {
        "count": 1,
        "liked": True,
    }
    assert client.delete(url, headers=headers).json()["data"] == {
        "count": 0,
        "liked": False,
    }


def test_every_viewer_gets_their_own_liked_flag(client):
    row = _report(client, REPORTER)
    comment = _comment(client, STRANGER, row["id"], "说得对")
    client.post(
        f"/feedback/{row['id']}/comments/{comment['id']}/likes",
        headers=session_auth_headers(REPORTER),
    )

    seen = {c["id"]: c for c in _thread(client, STRANGER, row["id"])}[comment["id"]]
    assert (seen["likes"], seen["liked"]) == (1, False)

    seen = {c["id"]: c for c in _thread(client, REPORTER, row["id"])}[comment["id"]]
    assert (seen["likes"], seen["liked"]) == (1, True)


def test_the_thread_and_the_like_route_never_disagree_about_the_count(client):
    row = _report(client, REPORTER)
    comment = _comment(client, STRANGER, row["id"], "说得对")
    url = f"/feedback/{row['id']}/comments/{comment['id']}/likes"

    for handle in (REPORTER, ADMIN, STRANGER):
        assert client.post(url, headers=session_auth_headers(handle)).status_code == 200

    listed = {c["id"]: c for c in _thread(client, STRANGER, row["id"])}[comment["id"]]
    assert listed["likes"] == 3
    # 详情页那一份 thread 也要说同一句话：它读的是同一个 `comments_out`，这条断言
    # 就是钉住那一句的。
    detail = client.get(
        f"/feedback/{row['id']}", headers=session_auth_headers(STRANGER)
    ).json()["data"]
    assert {c["id"]: c for c in detail["thread"]}[comment["id"]]["likes"] == 3


def test_a_like_still_lands_under_a_finished_report(client, as_admin):
    """点赞**不挡**已办完的反馈，理由和 `support` 挡它正好相对。

    支持数是「热门」的输入，让人往一条已经办完的东西上继续堆，堆的是一个没人能
    据此行动的排序 —— 那是反馈级那个回 412 的原因。点赞不参与任何排序，它说的是
    「这条回复说得对」，而结论（「原来是这样，我也遇到了」）恰恰长在办完了的反馈
    底下。在这儿加同一道闸，等于最后一批有用的回复是不许被标记的那一批。
    """
    row = _report(client, REPORTER)
    comment = _comment(client, STRANGER, row["id"], "根因是只读 token")
    client.post(
        f"/admin/feedback/{row['id']}/status",
        json={"status": "deployed"},
        headers=session_auth_headers(as_admin),
    )

    r = client.post(
        f"/feedback/{row['id']}/comments/{comment['id']}/likes",
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"count": 1, "liked": True}


# --- 并发的两条路 -------------------------------------------------------------
#
# `ON CONFLICT DO NOTHING` 在这两条上不是微优化，是正确性。被替换掉的写法是
# 「先 SELECT 有没有、再 INSERT」，两个重叠的请求都会读到「没有」，第二个撞唯一约束
# 回 500 —— 用户看到的是「点一下按钮把页面搞坏了」。单连接跑不出来（同一根连接上
# 两条语句本来就是串行的），所以这里从 `client.test_factory` 上开**真的几条连接**：
# 它的 engine 是 NullPool，每个 session 一根自己的连接。


async def _seed_thread(
    factory, *, handle: str = REPORTER
) -> tuple[uuid.UUID, uuid.UUID]:
    """一条反馈 + 一条顶层评论，直接落库。

    不是为了绕开接口，是因为这两个用例要的是并发：HTTP 那一路要经过 `client` 的
    portal，而 `asyncio.gather` 要的几条真连接得从 test_factory 上开。
    """
    async with factory() as session:
        repo = FeedbackRepository(session)
        row = await repo.add(
            title="并发",
            summary="并发",
            kind=FeedbackKind.bug,
            visibility=FeedbackVisibility.public,
            problem="并发",
            author_handle=handle,
            author_user_id=None,
            author_is_agent=False,
        )
        comment = await repo.add_comment(
            feedback_id=row.id,
            author_handle=handle,
            author_user_id=None,
            author_is_agent=False,
            body="顶楼",
            parent_id=None,
            reply_to_handle=None,
        )
        ids = (row.id, comment.id)
        await session.commit()
    return ids


async def test_eight_likes_at_once_leave_exactly_one_row(client):
    factory = client.test_factory
    _, comment_id = await _seed_thread(factory)

    async def like(handle: str) -> bool:
        async with factory() as session:
            landed = await FeedbackRepository(session).add_comment_like(
                comment_id, handle
            )
            await session.commit()
            return landed

    # 同一个人点八下：只有一下真的插进去，另外七下是 no-op 而不是七次报错。
    results = await asyncio.gather(*[like(REPORTER) for _ in range(8)])
    assert sum(1 for r in results if r) == 1

    async with factory() as session:
        assert await FeedbackRepository(session).comment_like_count(comment_id) == 1


async def test_supporting_twice_at_once_leaves_exactly_one_row(client):
    """反馈级的那个支持按钮，同一个毛病，同一副药。

    这条是修 `add_support` 时补的：评论点赞是新的，所以从第一天就用对的写法；支持
    是早就有的，改之前它一直在这个竞态里，而且从外面看和「按钮坏了」一模一样。
    """
    factory = client.test_factory
    feedback_id, _ = await _seed_thread(factory)

    async def support() -> bool:
        async with factory() as session:
            landed = await FeedbackRepository(session).add_support(
                feedback_id, STRANGER
            )
            await session.commit()
            return landed

    results = await asyncio.gather(*[support() for _ in range(8)])
    assert sum(1 for r in results if r) == 1

    async with factory() as session:
        assert await FeedbackRepository(session).supports_count(feedback_id) == 1


async def test_a_reply_whose_parent_is_gone_is_invisible_and_uncounted(client):
    """孤儿回复：父亲已经软删，而它自己的 `deleted_at` 还是 NULL。

    这个形状不用等竞态也能造出来 —— 级联跑完之后再插一条回复就是它。而竞态窗口里
    真实产生的那一行也长这样：外键检查拿 `FOR KEY SHARE`、删除那条 `UPDATE` 拿
    `FOR NO KEY UPDATE`，两者不冲突，所以同一瞬间插进来的回复不会被挡住。更早的
    版本还会**自己**留下这种行：那时删顶层只盖了它自己一行。

    读的时候必须挡住，因为客户端是「取顶层、再问每条的回复」—— 一条回复的父亲不在
    返回里，它就永远画不出来，于是这条评论数得出来、看不见、也没有按钮能删掉。
    """
    factory = client.test_factory
    feedback_id, top_id = await _seed_thread(factory)

    async with factory() as session:
        repo = FeedbackRepository(session)
        parent = await repo.get_comment(top_id)
        assert parent is not None
        # 级联带走的是**当时**已经存在的回复；跑完之后新插的这条正是孤儿。
        await repo.soft_delete_comment(parent)
        await repo.add_comment(
            feedback_id=feedback_id,
            author_handle=STRANGER,
            author_user_id=None,
            author_is_agent=False,
            body="父亲已经没了，我还活着",
            parent_id=top_id,
            reply_to_handle=None,
        )
        await session.commit()

    async with factory() as session:
        repo = FeedbackRepository(session)
        assert (await repo.page_comments(feedback_id)).rows == []
        assert await repo.comment_counts([feedback_id]) == {}

    # 三条读路径说的是同一件事：帖子、详情里的评论数、帖子里的那一条。
    assert _thread(client, REPORTER, str(feedback_id)) == []
    r = client.get(f"/feedback/{feedback_id}", headers=session_auth_headers(REPORTER))
    assert r.status_code == 200, r.text
    detail = r.json()["data"]
    assert detail["thread"] == []
    assert detail["comments"] == 0


async def test_a_reply_that_arrives_while_its_parent_is_being_deleted(client):
    """把竞态**跑一遍**，而且拿时间去量它 —— 不是从文档里抄一句「不冲突」。

    上一条用例钉的是「孤儿长什么样、三条读路径挡不挡得住」，它自己的 docstring 写明
    形状是手工造的。这一条钉的是另一半：**同一瞬间真的会生出一只孤儿**。少了它，
    「竞态会产生孤儿」就只是从 Postgres 的锁语义推出来的一个结论，没有任何一次执行
    支持它 —— 而这一批的可见性正确性恰恰架在这句话上面。

    窗口是这么撑开的：删除那条事务拿软删的 `UPDATE` 之后**故意不提交** `_HOLD_SECONDS`，
    回复就在这段时间里插进来。判据是**插入等了多久**：

    * 不排队（毫秒级）→ 外键检查对父行要的 `FOR KEY SHARE` 和软删要的
      `FOR NO KEY UPDATE` 确实不冲突，B 直着落地，A 提交时那遍级联 `UPDATE` 早就
      跑完了 —— 表里留下一条 `deleted_at IS NULL`、父亲却已经软删的行。
      `live_comment_clause()` 就是为它存在的。
    * 排队（等满约 `_HOLD_SECONDS`）→ 两者开始抢锁了，那么「读侧必须挡孤儿」的
      整个理由要重新审一遍：拦住它的会变成数据库，而不是那条 where。

    拿时间去量而不是只断言「表里有那一行」，是因为后者的两种情况看着一模一样 ——
    排队之后照样落地，于是这条用例会在锁语义变了的当天，安静地退化成「什么都没测」。

    **不变式与走哪条分支无关**：看不见的父亲带不出看得见的儿子。所以读路径验的是
    三个入口的一致性，而不是某一行在不在。
    """
    factory = client.test_factory
    feedback_id, top_id = await _seed_thread(factory)

    async def delete_and_hold() -> None:
        async with factory() as session:
            parent = await FeedbackRepository(session).get_comment(top_id)
            assert parent is not None
            await FeedbackRepository(session).soft_delete_comment(parent)
            # 锁还攥在这条没提交的事务里，回复要在这段时间里进来。
            await asyncio.sleep(_HOLD_SECONDS)
            await session.commit()

    async def reply_into_the_window() -> float:
        await asyncio.sleep(_HEAD_START_SECONDS)
        async with factory() as session:
            started = time.monotonic()
            await FeedbackRepository(session).add_comment(
                feedback_id=feedback_id,
                author_handle=STRANGER,
                author_user_id=None,
                author_is_agent=False,
                body="父亲正在被删，我这个时候进来",
                parent_id=top_id,
                reply_to_handle=None,
            )
            await session.commit()
            return time.monotonic() - started

    async with asyncio.timeout(_HOLD_SECONDS * 5):
        _, waited = await asyncio.gather(delete_and_hold(), reply_into_the_window())

    assert waited < _HOLD_SECONDS / 2, (
        f"插入等了 {waited:.2f}s，而删除把锁攥了 {_HOLD_SECONDS}s：外键检查和软删"
        "开始抢锁了。`live_comment_clause()` 的理由（数据库挡不住，所以读侧挡）"
        "要重新审一遍再改这里的判据。"
    )

    async with factory() as session:
        # 表里到底留下了什么。说明这次测量，不参与不变式。
        survivors = list(
            (
                await session.execute(
                    select(FeedbackComment.id).where(
                        FeedbackComment.parent_id == top_id,
                        FeedbackComment.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        # 数据库没挡住，活下来的那一条就在表里 —— 读侧必须挡住它。
        assert survivors, "竞态在窗口里落地了，表里却没有那一行"

        repo = FeedbackRepository(session)
        visible = (await repo.page_comments(feedback_id)).rows
        # 不变式：返回的每一条，父亲都在返回里。
        top_ids = {c.id for c in visible if c.parent_id is None}
        assert all(c.parent_id in top_ids for c in visible if c.parent_id is not None)
        assert await repo.comment_counts([feedback_id]) == {}

    # 三条读路径说的是同一件事：帖子、详情里的评论数、楼里的那一条。
    assert _thread(client, STRANGER, str(feedback_id)) == []
    r = client.get(f"/feedback/{feedback_id}", headers=session_auth_headers(STRANGER))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["thread"] == []
    assert r.json()["data"]["comments"] == 0


# --- 评论分页 ---------------------------------------------------------------


async def _seed_comments(
    factory, *, tops: int = 0, replies: int = 0
) -> tuple[uuid.UUID, list[uuid.UUID], list[uuid.UUID]]:
    """直接落库造一条反馈：`tops` 条顶层评论，第一条下面挂 `replies` 条回复。

    不走 HTTP，因为这几个用例要的是**条数**（跨过一页的上限、跨过分批的块），一条
    一次请求往返在这里只是把时间花在网络上。返回 `(反馈 id, 顶层 id, 回复 id)`，
    顺序都是插入顺序（也就是服务端排的那个顺序）。
    """
    async with factory() as session:
        repo = FeedbackRepository(session)
        row = await repo.add(
            title="分页",
            summary="分页",
            kind=FeedbackKind.bug,
            visibility=FeedbackVisibility.public,
            problem="分页",
            author_handle=REPORTER,
            author_user_id=None,
            author_is_agent=False,
        )
        top_ids = []
        for i in range(tops):
            top = await repo.add_comment(
                feedback_id=row.id,
                author_handle=REPORTER,
                author_user_id=None,
                author_is_agent=False,
                body=f"顶楼 {i}",
                parent_id=None,
                reply_to_handle=None,
            )
            top_ids.append(top.id)
        reply_ids = []
        for i in range(replies):
            reply = await repo.add_comment(
                feedback_id=row.id,
                author_handle=STRANGER,
                author_user_id=None,
                author_is_agent=False,
                body=f"回复 {i}",
                parent_id=top_ids[0],
                reply_to_handle=None,
            )
            reply_ids.append(reply.id)
        ids = (row.id, top_ids, reply_ids)
        await session.commit()
    return ids


async def test_a_cursor_walks_a_thread_even_when_every_timestamp_is_identical(client):
    """翻页不漏不重 —— 包括 `created_at` 全撞在一起的时候。

    同一时间戳不是硬造出来的角落：`NOW()` 在一条语句里对每一行是同一个值，批量导入
    和种子数据一页全是同一个时间戳。只按 `created_at` 排序时，同一时刻的几条在两次
    查询里的先后可以不一样，翻页于是漏行、或者把同一行发两遍；把 `id` 并进游标才是
    全序。这个用例把所有顶层评论的时间戳**钉成同一个值**，再一页一页翻到底。
    """
    factory = client.test_factory
    feedback_id, top_ids, _ = await _seed_comments(factory, tops=7)
    same_instant = datetime(2026, 1, 1, tzinfo=UTC)
    async with factory() as session:
        await session.execute(
            update(FeedbackComment)
            .where(FeedbackComment.feedback_id == feedback_id)
            .values(created_at=same_instant)
        )
        await session.commit()

    seen: list[uuid.UUID] = []
    after: str | None = None
    async with factory() as session:
        repo = FeedbackRepository(session)
        for _ in range(10):
            page = await repo.page_comments(feedback_id, after=after, limit=3)
            seen.extend(row.id for row in page.rows)
            if page.next_cursor is None:
                break
            after = page.next_cursor
        else:  # pragma: no cover - 只有翻页不收敛才会走到这里
            pytest.fail("游标没有翻到底：同一时刻的那几条没有全序")

    # 七条各来一次。漏掉一条是「翻页丢行」，多一条是「游标把边界又发了一遍」。
    assert sorted(seen) == sorted(top_ids)


async def test_a_building_gives_its_replies_in_pages_and_says_how_many_it_has(client):
    """楼内回复自己一页，而**一页带了几条**和**这栋楼一共有几条**是两件事。

    `reply_counts` 是后者，`reply_cursors` 是「还有的话从哪儿接着取」。两个都对着
    「正好取完」的边界：`page_comments` 每栋楼多取一条只为回答「还有没有下一页」，
    少了那一条，一栋正好 2 条的楼会被判成还有下一页，客户端于是发一次必然取到空页
    的请求。
    """
    factory = client.test_factory
    feedback_id, top_ids, reply_ids = await _seed_comments(factory, tops=1, replies=5)
    async with factory() as session:
        repo = FeedbackRepository(session)
        page = await repo.page_comments(feedback_id, replies_limit=2)
        assert page.reply_counts == {top_ids[0]: 5}
        assert [row.id for row in page.rows if row.parent_id] == reply_ids[:2]
        cursor: str | None = page.reply_cursors[top_ids[0]]
        assert cursor is not None
        rest: list[uuid.UUID] = []
        for _ in range(10):
            rows, cursor = await repo.page_replies(top_ids[0], after=cursor, limit=2)
            rest.extend(row.id for row in rows)
            if cursor is None:
                break
        else:  # pragma: no cover
            pytest.fail("楼内的游标没有翻到底")
        assert rest == reply_ids[2:]

    # 正好取完：不留一个「下一页是空的」游标。
    async with factory() as session:
        exact = await FeedbackRepository(session).page_comments(
            feedback_id, replies_limit=5
        )
    assert exact.reply_counts == {top_ids[0]: 5}
    assert exact.reply_cursors == {}


def test_the_thread_tells_the_client_how_many_replies_a_building_has(client):
    """这两个数必须真的**到得了线上**。

    `CommentOut.from_row` 收下 `reply_count` 却忘了往构造器里传，是这条路上真实发生
    过一次的事故形状：字段在 schema 里、注释写得很清楚、测试也全绿（没有一条断言
    它），而每一个响应里它都是 0 —— 客户端拿到的「这栋楼有 0 条回复」和屏幕上那
    三条回复对不上，「展开更多」于是永远不发第二次请求。
    """
    row = _report(client, REPORTER)
    top = _comment(client, REPORTER, row["id"], "顶楼")
    for i in range(3):
        _comment(client, STRANGER, row["id"], f"回复 {i}", parent_id=top["id"])

    r = client.get(
        f"/feedback/{row['id']}/comments", headers=session_auth_headers(REPORTER)
    )
    assert r.status_code == 200, r.text
    payload = r.json()["data"]
    by_id = {c["id"]: c for c in payload["items"]}
    assert by_id[top["id"]]["reply_count"] == 3
    # 三条都跟着这一页回来了，所以楼内没有下一页 —— 游标只在真有下一页时有值，
    # 客户端据此知道「展开更多」是摊开手上这些，还是去取下一页。
    assert by_id[top["id"]]["replies_next_cursor"] is None
    # 顶层评论这一页也只有一页。
    assert payload["next_cursor"] is None
    # 回复自己不带这两个字段的含义：恒为 0 / 恒为 None（它们没有楼）。
    replies = [c for c in payload["items"] if c["parent_id"]]
    assert [c["reply_count"] for c in replies] == [0, 0, 0]
    assert [c["replies_next_cursor"] for c in replies] == [None, None, None]

    # 楼内那一页走的也是这条路由，只是给了 `parent_id`。
    r = client.get(
        f"/feedback/{row['id']}/comments",
        params={"parent_id": top["id"]},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    assert [c["body"] for c in r.json()["data"]["items"]] == [
        "回复 0",
        "回复 1",
        "回复 2",
    ]


async def test_paging_the_thread_does_not_shrink_the_count_on_the_card(client):
    """卡片上那个数字是**这条反馈一共有几条评论**，不是这一页有几条。

    分页之前 `comments` 是 `len(thread)`；分页之后那就是一页的大小，卡片上的数字会
    随翻页往下掉 —— 屏幕上是「评论 50」，翻一次变成「评论 21」，而一条评论都没少。
    """
    factory = client.test_factory
    feedback_id, top_ids, _ = await _seed_comments(
        factory, tops=feedback_repo.THREAD_PAGE + 1
    )
    r = client.get(f"/feedback/{feedback_id}", headers=session_auth_headers(REPORTER))
    assert r.status_code == 200, r.text
    detail = r.json()["data"]
    assert len(detail["thread"]) == feedback_repo.THREAD_PAGE
    assert detail["comments"] == feedback_repo.THREAD_PAGE + 1
    assert detail["thread_next_cursor"] is not None


def test_a_cursor_that_is_not_ours_is_a_400_not_a_500(client):
    """坏游标是调用方递进来的东西，坏在它那一侧。"""
    row = _report(client, REPORTER)
    _comment(client, REPORTER, row["id"], "顶楼")
    r = client.get(
        f"/feedback/{row['id']}/comments",
        params={"after": "昨天下午"},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 400, r.text


def test_replies_cannot_be_pulled_across_reports(client):
    """一栋楼里的回复，只能从**它自己那条反馈**的地址里取。

    可见性是按帖子判的（`visible_row`），而楼内那一页是按 `parent_id` 取的。少了
    「父亲必须属于被点名的那条反馈」这一步，把自己这条公开帖子的地址配上别人私密
    报告里某条评论的 id，取回来的就是那份私密报告里的对话。
    """
    mine = _report(client, REPORTER)
    my_top = _comment(client, REPORTER, mine["id"], "我这边的顶楼")

    secret = _report(client, STRANGER, visibility="private")
    secret_top = _comment(client, STRANGER, secret["id"], "私密楼")
    _comment(
        client, STRANGER, secret["id"], "只该被自己人看见", parent_id=secret_top["id"]
    )

    r = client.get(
        f"/feedback/{mine['id']}/comments",
        params={"parent_id": secret_top["id"]},
        headers=session_auth_headers(REPORTER),
    )
    # 404 而不是 403：连「这条评论存在」都不确认。
    assert r.status_code == 404, r.text
    assert "只该被自己人看见" not in r.text

    # 同一个地址配自己的楼，照常出结果 —— 挡住的是跨帖，不是这个参数。
    r = client.get(
        f"/feedback/{mine['id']}/comments",
        params={"parent_id": my_top["id"]},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["items"] == []


async def test_chunked_in_queries_answer_the_same_as_one_big_one(client, monkeypatch):
    """分批之后**合起来**的答案和一次问完一样。

    `_IN_BATCH` 的由来是 asyncpg 把参数个数编进 int16，超过 32767 条直接抛
    `InterfaceError` —— 那条线不该靠造三万条评论去验证。把批大小压到 2，走的是同一
    条回路：切、逐批查、合并。合并写错（覆盖而不是并集）在真实规模下只会表现为
    「一大片评论的点赞数突然都是 0」，很难从现象倒回来。
    """
    factory = client.test_factory
    feedback_id, top_ids, _ = await _seed_comments(factory, tops=5)
    monkeypatch.setattr(feedback_repo, "_IN_BATCH", 2)
    async with factory() as session:
        repo = FeedbackRepository(session)
        assert await repo.add_comment_like(top_ids[0], STRANGER)
        assert await repo.add_comment_like(top_ids[3], STRANGER)
        await session.commit()

    async with factory() as session:
        repo = FeedbackRepository(session)
        counts = await repo.comment_like_counts(top_ids)
        liked = await repo.comment_liked_by(top_ids, STRANGER)

    assert counts == {top_ids[0]: 1, top_ids[3]: 1}
    assert liked == {top_ids[0], top_ids[3]}


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


def test_the_card_it_proposed_is_not_an_input_it_has_to_read(client):
    """提案卡是芝士自己落下的，等的是**人**按那两个按钮，不是它自己读一遍。

    和 `/ask` 同一条路：卡是一条 kind=message、署名是 agent 的块，而这个端点也填
    不出轮次号（CLI 只在 CHEESE_TURN 非空时才带 X-Cheese-Turn）。按「署名是 agent
    且落在某一轮里」去算它就成了待读输入，「忘了 @」的补救按钮于是白开一轮，把芝
    士自己提的那张卡当成一条没读过的话喂回去。
    """
    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    token = mint_scoped_token(project_id=project, topic_id=topic)

    assert _propose(client, topic, token).status_code == 200

    summoned = client.post(f"/topics/{topic}/summon", json={"author": REPORTER})
    assert summoned.status_code == 200, summoned.text
    assert summoned.json()["data"] == {
        "started": False,
        "reason": "nothing_pending",
    }, "它自己提的那张卡不该把它自己叫起来"


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
    # The whole ladder, not just a membership check: the front end falls back to
    # its own copy only when this is missing, so a rung added in `models.py` and
    # not served here renders as a status chip with no label — and the two
    # retired rungs (`triaging`, `planned`) must not come back through here,
    # because `set_status` would then accept them into a `VARCHAR(16)` that has
    # no database-side enum to stop it.
    assert anon["status_ladder"] == [
        "received",
        "in_progress",
        "resolved",
        "deployed",
    ]
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


def test_a_screen_credential_on_the_admin_list_is_refused(client, monkeypatch):
    """§4.3's second gate, and why it has to be in the route body.

    `/admin/*` is not in `_CHEESE_WRITE_PATHS`, and that table is a whitelist —
    nothing in the middleware looks at this prefix, so the refusal has to be
    written here or it does not exist. The handle below is **on the platform
    admin list**, so the allow-list is not what refuses it.

    What refuses it here is the credential. The same handle arrives twice: once
    on its own session token and once on a device screen's token, a per-screen
    capability that resolves on any path (unlike a per-turn `cheese` credential,
    which the resolver refuses when there is no project to scope it to). One
    handle, two credentials, two answers — 「管理动作由本人在自己的会话里做」.
    The handle itself carries no agent-binding, which is what makes this half
    about the credential alone; the binding half is the test below.
    """
    agent = "agent-on-the-list"
    monkeypatch.setattr(settings, "platform_admin_handles", [agent])
    screen = _register_screen(handle=agent)
    try:
        allowed = client.get("/admin/feedback", headers=session_auth_headers(agent))
        assert allowed.status_code == 200, allowed.text

        refused = client.get(
            "/admin/feedback", headers={"X-Cheese-Screen": screen.token}
        )
        assert refused.status_code == 403, refused.text
        # 拒的是凭证，说出口的也得是凭证——写「agent 不能」会把「按种类拒」重新钉回来。
        assert "凭证" in refused.json()["message"]

        # A write route too: every handler in the module goes through one helper,
        # and this is what says the helper is actually on them.
        wrote = client.post(
            f"/admin/feedback/{_report(client, REPORTER)['id']}/status",
            json={"status": "in_progress"},
            headers={"X-Cheese-Screen": screen.token},
        )
        assert wrote.status_code == 403, wrote.text
    finally:
        _unregister(screen)


def test_an_agent_on_the_admin_list_is_refused_on_its_own_session(client, monkeypatch):
    """§4.3's other half: 「管理动作 agent 不能做」, asked of the participant.

    The allow-list is a list of handles and nothing stops an agent's from being
    on it, so this is the case where the credential says nothing: a session
    token, no scope, nothing unattended about it. What refuses the call is the
    agent-binding the handle carries, read where the question is being asked
    rather than travelled in on the actor.
    """
    project = _project(client, REPORTER)
    made = client.post(
        f"/projects/{project}/agents",
        json={"handle": "planner", "display_name": "规划师"},
    )
    assert made.status_code == 200, made.text
    agent = agent_instance_handle(made.json()["data"]["id"])
    monkeypatch.setattr(settings, "platform_admin_handles", [agent, REPORTER])

    refused = client.get("/admin/feedback", headers=session_auth_headers(agent))
    assert refused.status_code == 403, refused.text
    assert "agent" in refused.json()["message"]

    # The control: a person on the same list gets in, so what refused the call
    # above is the binding and not the list.
    allowed = client.get("/admin/feedback", headers=session_auth_headers(REPORTER))
    assert allowed.status_code == 200, allowed.text


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


# --- 作者头像 ---------------------------------------------------------------
#
# 头像不在反馈行上，在 `UserProfile` 上，所以要解析；而注册的每条路径都写死
# ``default_avatar_id=1``，所以「档案上有个头像 id」不等于「这个人挑过头像」。两条
# 合起来才是这一组：挑过的给 id，没挑过的给 null，客户端才画得出彩色首字母。


def _seed_profiles(client, picks: dict[str, str]) -> dict[str, int]:
    """给这些 handle 落一份用户档案，返回各自头像素材的 id。

    `picks` 是 handle → ``avatar_type``：``"default"`` 就是注册时人人被写上的那一张，
    ``"predefined"`` / ``"upload"`` 才是本人挑的、传的。测试库里没有种子头像
    （``_seed_reference_data`` 只种表情类型），所以这里自己造行 —— 也正因为如此，
    「默认头像到底是哪一行」在这由测试说了算，而不是一个写死的 1。
    """
    import asyncio
    from datetime import UTC, datetime

    from app.domain.avatars.models import Avatar
    from app.domain.user.models import User, UserProfile

    ids: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            for handle, avatar_type in picks.items():
                avatar = Avatar(
                    url="",
                    name=f"{avatar_type}.png",
                    avatar_type=avatar_type,
                    created_at=now,
                    usage_count=0,
                )
                s.add(avatar)
                await s.flush()
                ids[handle] = avatar.id
                user = User(
                    username=handle,
                    email=f"{handle}@example.com",
                    created_at=now,
                    updated_at=now,
                )
                s.add(user)
                await s.flush()
                s.add(
                    UserProfile(
                        user_id=user.id,
                        nickname=handle.upper(),
                        intro="",
                        avatar_id=avatar.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await s.commit()

    asyncio.run(_seed())
    return ids


def test_cards_report_the_avatar_its_author_picked_and_null_for_everyone_else(client):
    """列表上的作者头像：挑过的人给 id，没挑过、没档案的人给 null。

    三种情况各是一半的坑：

    * 挑过 → 给 id。头像不在反馈行上，不解析就永远没有。
    * 没挑过 → **null**。注册时人人都被写上全局默认头像，照原样发出去，所有没挑过的
      人共用同一张脸；按 handle 派生的彩色首字母至少彼此不同，而认人正是头像唯一的活。
    * 没有用户档案（cheesex 会话身份、agent 座位）→ 也是 null，不是报错。
    """
    ids = _seed_profiles(client, {"fb-picked": "predefined", "fb-plain": "default"})
    _report(client, "fb-picked", title="挑过头像的人提的")
    _report(client, "fb-plain", title="没挑过头像的人提的")
    _report(client, "fb-nobody", title="没有档案的人提的")

    rows = {c["title"]: c for c in _cards(client)}
    assert rows["挑过头像的人提的"]["author_avatar_id"] == ids["fb-picked"]
    assert rows["没挑过头像的人提的"]["author_avatar_id"] is None
    assert rows["没有档案的人提的"]["author_avatar_id"] is None


def test_the_chat_roster_and_the_feedback_card_report_the_same_face(client):
    """工作台聊天区读的那份名册和反馈读的是**同一个判断**，不是两条。

    聊天区画的是 `props.members`，也就是 `GET /projects/{id}/members`；反馈画的是
    `author_avatar_id`。两边各自从 `UserProfile` 解析头像，而「挑过没有」这条规则
    只要有一侧写得不一样（名册那边哪天改成直接发 `avatar_id`，或者这里改用别的查法），
    表现就是**同一个人在聊天区有脸、在反馈里是彩色首字母** —— 两套门禁都不会红，
    因为两边各自都「对」。

    所以这里比的不是「都非空」，是**同一份答案**：挑过的人在两边拿到同一个 id，
    没挑过的人在两边都拿到 null。判据本身在 `chosen_avatar_ids` 和
    `ProjectRepository.people`，两处都按 `Avatar.avatar_type` 认默认图 ——
    两边都写了、都写了注释，这个用例是唯一能拦住它们漂开的东西。
    """
    ids = _seed_profiles(client, {"fb-picked": "predefined", "fb-plain": "default"})
    project = _project(client, "fb-picked")
    client.post(
        f"/projects/{project}/members",
        json={"user_handle": "fb-plain"},
        headers=session_auth_headers("fb-picked"),
    )
    _report(client, "fb-picked", title="挑过头像的人提的")
    _report(client, "fb-plain", title="没挑过头像的人提的")

    roster = {
        m["user_handle"]: m["avatar_id"]
        for m in client.get(
            f"/projects/{project}/members", headers=session_auth_headers("fb-picked")
        ).json()["data"]["data"]
    }
    cards = {c["title"]: c["author_avatar_id"] for c in _cards(client, "fb-picked")}

    assert roster["fb-picked"] == ids["fb-picked"]
    assert cards["挑过头像的人提的"] == ids["fb-picked"]
    # 没挑过的那一半：两侧都必须是 null，而不是各自发一个默认头像 id。
    assert roster["fb-plain"] is None
    assert cards["没挑过头像的人提的"] is None


def test_every_face_the_detail_draws_is_resolved_the_same_way(client, as_admin):
    """详情页一屏里有三种作者：报告的作者、评论的作者、内部备注的作者。

    三条路都走同一张 handle → 头像的表（`FeedbackDetail.from_row`）。分成三处各查一次
    就是评论一多就 N+1，而且很容易只补上其中一条 —— 那正是「同一个人在列表里有头像、
    在评论里变成首字母」的来源。这里把三条路都走一遍，包括管理端列表那条。
    """
    ids = _seed_profiles(
        client, {REPORTER: "upload", ADMIN: "predefined", "fb-plain": "default"}
    )
    row = _report(client, REPORTER, title="详情页上的三张脸")
    fid = row["id"]
    assert row["author_avatar_id"] == ids[REPORTER]

    assert (
        client.post(
            f"/feedback/{fid}/comments",
            json={"body": "补一句"},
            headers=session_auth_headers(REPORTER),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/admin/feedback/{fid}/notes",
            json={"body": "内部备注"},
            headers=session_auth_headers(ADMIN),
        ).status_code
        == 200
    )

    # 非管理员：评论作者带头像，备注整段看不见（不是空字符串，是空数组）。
    seen = client.get(
        f"/feedback/{fid}", headers=session_auth_headers("fb-plain")
    ).json()["data"]
    assert seen["thread"][0]["author_avatar_id"] == ids[REPORTER]
    assert seen["notes"] == []

    # 管理员：备注作者也有头像 —— 备注是另一张 `from_row`（`NoteOut`）。
    admin_detail = client.get(
        f"/admin/feedback/{fid}", headers=session_auth_headers(ADMIN)
    ).json()["data"]
    assert admin_detail["notes"][0]["author_avatar_id"] == ids[ADMIN]

    # 管理端列表走的是自己那份 `_cards`，也要解析，否则同一张卡在两个列表里长得不一样。
    listed = client.get("/admin/feedback", headers=session_auth_headers(ADMIN)).json()[
        "data"
    ]["data"]
    assert listed[0]["author_avatar_id"] == ids[REPORTER]


def test_a_search_reaches_the_body_and_the_author(client, as_admin):
    """搜索匹配的四列是**标题 + 摘要 + 正文 + 作者**，两个列表共用这一个判据。

    摘要只是正文开头那几十个字，所以有两样东西只搜标题 + 摘要谁都找不回来：正文
    中段那句话，和「这是谁提的」。而这两样恰恰是提交者回头找自己那条时会用的
    —— 他记得的是自己写过的一句话，不是当初随手填的标题。

    管理端那一半也钉在这里：两处是两个搜索框，却是同一个问题。判据写成两份之后
    漂开的表现就是「管理端搜得到、反馈中心搜不到」，而报这个毛病的人在比的正是
    这两屏。
    """
    mine = _report(
        client,
        REPORTER,
        title="滚动位置丢了",
        problem="翻到第三页再返回，位置回到最顶上，只能重新翻一遍",
    )
    _report(
        client,
        STRANGER,
        title="头像一直是灰的",
        problem="换过头像之后还要刷新一次才显示",
    )

    def ids(**params: str) -> set[str]:
        return {card["id"] for card in _cards(client, REPORTER, **params)}

    # 正文中段的话：`summary` 里没有它，标题里更没有。
    assert ids(q="第三页再返回") == {mine["id"]}
    # 作者：提交者找自己的那条时最自然的一个词。
    assert ids(q=REPORTER) == {mine["id"]}
    assert ids(q="这个词谁都没有") == set()

    listed = client.get(
        "/admin/feedback",
        params={"q": "第三页再返回"},
        headers=session_auth_headers(ADMIN),
    ).json()["data"]["data"]
    assert {card["id"] for card in listed} == {mine["id"]}


# --- 成员管理：名单两份来源、加进去的人当场生效、根删不掉 --------------------
#
# 这里建的是**真账号**（`seed_user` 直接落库并提交），不是 `session_auth_headers`
# 那种只带 handle 的令牌：能加进名单的前提是平台里真有这个人，而「有」与「没有」
# 正是这几条用例要分开的两件事。


def _admins(client, handle: str) -> dict:
    r = client.get("/admin/admins", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _add_admin(client, *, by: str, target: str):
    return client.post(
        "/admin/admins", json={"handle": target}, headers=session_auth_headers(by)
    )


def _searched(client, handle: str, q: str) -> list[dict]:
    """加人那个选择器看到的候选（`GET /admin/users`）。"""
    r = client.get(
        "/admin/users", params={"q": q}, headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["items"]


def _nickname_user(client, handle: str, nickname: str) -> None:
    """给一个真人写上昵称，选择器「按名字搜」的那一半才有东西可搜。

    直接写库：注册那条路要邮箱验证码，而这里要的只是「user_profile 里有一行」
    这一件事（`seed_user` 建人的时候也没写 profile，所以这里自己补一条）。
    """
    from app.domain.user.models import UserProfile
    from app.domain.user.repositories import UserRepository

    async def _write() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            now = datetime.now(UTC)
            session.add(
                UserProfile(
                    user_id=user.id,
                    nickname=nickname,
                    intro="",
                    avatar_id=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(_write())


def test_the_roster_is_two_lists_and_only_the_added_one_can_be_edited(client, as_admin):
    """配置里那份列得出来、删不掉；页面上加的那份可删，删一个不在名单里的不是错。"""
    from tests.conftest import seed_user

    seed_user(client, "fb-hired")
    assert _admins(client, as_admin) == {"root": [ADMIN], "added": []}

    added = _add_admin(client, by=as_admin, target="fb-hired")
    assert added.status_code == 200, added.text
    body = added.json()["data"]
    assert body["created"] is True
    assert [(row["handle"], row["added_by_handle"]) for row in body["added"]] == [
        ("fb-hired", as_admin)
    ]
    # `root` 那一份不受影响：加一个人不会把他挪成根管理员。
    assert body["root"] == [ADMIN]

    # 根管理员删不掉 —— 它是部署配置，改它要有服务器权限。409 而不是静默不动：
    # 真按到了说明页面和服务端对不上，那就该说出来。
    refused = client.delete(
        f"/admin/admins/{as_admin}", headers=session_auth_headers(as_admin)
    )
    assert refused.status_code == 409, refused.text
    assert _admins(client, as_admin)["root"] == [ADMIN]

    gone = client.delete(
        "/admin/admins/fb-hired", headers=session_auth_headers(as_admin)
    )
    assert gone.status_code == 200, gone.text
    assert gone.json()["data"]["removed"] is True
    assert gone.json()["data"]["added"] == []

    # 删一个已经不在名单里的人不是错误：他要的结果（这个人不在名单里）已经成立。
    again = client.delete(
        "/admin/admins/fb-hired", headers=session_auth_headers(as_admin)
    )
    assert again.status_code == 200
    assert again.json()["data"]["removed"] is False


def test_whoever_the_page_added_is_an_admin_on_their_next_request(client, as_admin):
    """加完就生效，不重启也不再改配置 —— 判据是「根 ∪ 表」，不是只有根。

    这条钉的是**两个来源真的合成了一个答案**：只读配置的话，页面上加的人会出现在
    名单里却什么也打不开（名单说他在，接口说他不是）；只读表的话，根管理员反而
    进不去。
    """
    from tests.conftest import seed_user

    seed_user(client, "fb-hired")
    private = _report(client, REPORTER, visibility="private")

    def is_admin(handle: str) -> bool:
        r = client.get("/feedback/meta", headers=session_auth_headers(handle))
        assert r.status_code == 200, r.text
        return bool(r.json()["data"]["is_admin"])

    def open_private(handle: str):
        return client.get(
            f"/feedback/{private['id']}", headers=session_auth_headers(handle)
        )

    assert is_admin("fb-hired") is False
    assert (
        client.get("/admin/feedback", headers=session_auth_headers("fb-hired"))
    ).status_code == 403
    assert open_private("fb-hired").status_code == 404

    assert _add_admin(client, by=as_admin, target="fb-hired").status_code == 200

    assert is_admin("fb-hired") is True
    assert (
        client.get("/admin/feedback", headers=session_auth_headers("fb-hired"))
    ).status_code == 200
    # 私密反馈对他是真的打开了：名单生效不只是改了一个布尔值。
    assert open_private("fb-hired").status_code == 200


def test_the_page_refuses_names_that_would_leave_the_roster_wrong(client, as_admin):
    """三种拒绝，各自对应一种「名单上有他但他进不来 / 进得来而名单骗人」。"""
    # 空白：名单按 handle 精确匹配，空串谁也匹配不上。
    assert _add_admin(client, by=as_admin, target="   ").status_code == 400
    # 平台上没有这个账号：写进去的表现是「名单里有人」而那个人根本不存在。
    assert _add_admin(client, by=as_admin, target="fb-nobody-at-all").status_code == 400
    # 根管理员不用再在页面上加一遍：加进去会落一行删不掉的重复，页面上显示两遍。
    assert _add_admin(client, by=as_admin, target=ADMIN).status_code == 409


def test_an_agent_is_a_refusal_in_the_add_form_and_absent_from_the_picker(
    client, as_admin
):
    """agent 当不了管理员（管理动作 agent 不能做），选择器里也不该出现它。

    这两件事要一起钉：只钉「加它是 400」的话，写错成「平台里没有这个账号」也照样
    绿 —— 而那条提示会把人送去查拼写，真正的原因却是这个身份不能有权限。
    """
    from tests.conftest import seed_user

    project = _project(client, REPORTER)
    topic = _topic(client, project, REPORTER)
    agent = room_agent_seat(client, topic)

    refused = _add_admin(client, by=as_admin, target=agent)
    assert refused.status_code == 400, refused.text
    assert "agent" in refused.json()["message"]

    # 同一个搜索找得到真人，却找不到 agent —— 否则上面那条「搜不到」是空搜索在过关。
    seed_user(client, "cheese-human")
    hits = {row["handle"] for row in _searched(client, as_admin, "cheese")}
    assert "cheese-human" in hits
    assert agent not in hits


def test_the_roster_is_not_something_a_stranger_can_read_or_change(client, as_admin):
    """四个端点全部要管理员。

    拒的理由不是「这个页面不该被看见」，而是**这份名单决定了谁能看见私密反馈和
    安全问题** —— 能读它就知道谁能看所有人的私密条目，能改它就能给自己开门。
    """
    calls = [
        ("GET", "/admin/admins", None),
        ("GET", "/admin/users?q=a", None),
        ("POST", "/admin/admins", {"handle": STRANGER}),
        ("DELETE", f"/admin/admins/{ADMIN}", None),
    ]
    for method, path, body in calls:
        theirs = client.request(
            method, path, json=body, headers=session_auth_headers(STRANGER)
        )
        assert theirs.status_code == 403, (method, path, theirs.text)
        # 没登录也一样：这不是「页面看不见」，是名单本身不给外人看。
        anonymous = client.request(method, path, json=body)
        assert anonymous.status_code in (401, 403), (method, path, anonymous.text)

    # 空搜索词是 400：空串搜出的是「平台的前 20 个账号」，那不是搜索结果。
    assert (
        client.get(
            "/admin/users", params={"q": ""}, headers=session_auth_headers(as_admin)
        ).status_code
        == 400
    )


def test_the_picker_searches_by_handle_and_by_nickname(client, as_admin):
    """选择器的搜索在 SQL 里、按两列搜，页面上的「搜不到」只有两种意思。

    复用 `GET /users?q=` 是不行的：那条接口先把一页 profile 取出来再在 Python 里
    过滤，所以搜索只在那一页里成立 —— 加人的时候「搜不到」就成了第三件事（这个人
    在，只是不在这一页），而界面上三件事长得一模一样。
    """
    from tests.conftest import seed_user

    seed_user(client, "fb-peng")
    _nickname_user(client, "fb-peng", "彭文博")
    seed_user(client, "fb-cat")

    by_name = _searched(client, as_admin, "彭文博")
    assert [(row["handle"], row["nickname"]) for row in by_name] == [
        ("fb-peng", "彭文博")
    ]
    assert by_name[0]["already_admin"] is False
    # 只记得 handle 也搜得到。
    assert [row["handle"] for row in _searched(client, as_admin, "fb-cat")] == [
        "fb-cat"
    ]
    assert _searched(client, as_admin, "这个人肯定没有") == []

    # 已经在名单里的人**照常出现**，带 already_admin —— 选择器据此画「已选中」，
    # 而不是画成「没有这个人」：后者会让人以为名单已经变了。
    assert _add_admin(client, by=as_admin, target="fb-peng").status_code == 200
    assert _searched(client, as_admin, "彭文博")[0]["already_admin"] is True
