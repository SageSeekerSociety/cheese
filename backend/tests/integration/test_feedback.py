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
import uuid
from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.device_hub import HubScreen, device_hub
from app.domain.feedback.models import Feedback, FeedbackKind, FeedbackVisibility
from app.domain.feedback.repositories import FeedbackRepository
from app.domain.identity.handles import agent_instance_handle, looks_like_agent_handle
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
    r = client.get(
        f"/feedback/{feedback_id}/comments", headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


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
        assert await repo.list_comments(feedback_id) == []
        assert await repo.comment_counts([feedback_id]) == {}

    # 三条读路径说的是同一件事：帖子、详情里的评论数、帖子里的那一条。
    assert _thread(client, REPORTER, str(feedback_id)) == []
    r = client.get(f"/feedback/{feedback_id}", headers=session_auth_headers(REPORTER))
    assert r.status_code == 200, r.text
    detail = r.json()["data"]
    assert detail["thread"] == []
    assert detail["comments"] == 0


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
    monkeypatch.setattr(settings, "feedback_admin_handles", [agent])
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
    monkeypatch.setattr(settings, "feedback_admin_handles", [agent, REPORTER])

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
