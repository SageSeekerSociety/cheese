"""App 采纳等 CI 再合 —— 绑定 GitHub 的项目上，采纳是授权，不是立刻合并。

这条路（`ForgeKind.github_app`）以前读一次 check-runs、把状态写进卡片 note、
然后立刻调合并 API。那句 note 是如实留痕，不是拦截：PR #414 开出 25 秒后就进了
main，而最后一项检查比合并晚 16 分钟。绿是运气，门禁根本没等。

这里钉的是**性质**，不是产物：断言「红的真的合不进去」「合并 API 一次都没被调
用」「没有 CI 跑过的改动不会被机器合掉」，而不是断言某个字段等于某个字符串。

GitHub 全程是 test double：开 PR 走 `pr_publish.GitHubPRClient`（App 那只），
之后的轮询走 `github_pr.default_client()`（协议那只，与 `github_user` 那条路复用
同一个 fake，所以两条路是被同一套假 GitHub 证明的）。
"""

from pathlib import Path

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.review import github_pr
from tests.conftest import wait_turns_idle
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept_pr import FakeGitHubPrClient

REPO = "acme/widgets"


def _make_project(client) -> str:
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post(
        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _accept(client, card_id: str, handle: str = "alice"):
    return client.post(
        f"/api/accept-cards/{card_id}/accept",
        json={"decided_by": handle},
        headers=session_auth_headers(handle),
    )


def _cards(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/accept-card").json()["data"]["data"]


def _topic(client, topic_id: str) -> dict:
    return client.get(f"/api/topics/{topic_id}").json()["data"]


def _poll(client) -> dict:
    r = client.post("/api/admin/scheduler/poll-open-prs")
    assert r.status_code == 200
    return r.json()["data"]


class _FakeTokens:
    """平台 GitHub App 的 installation token。轮询这条路上唯一该用的凭据。"""

    minted_write = 0

    async def write_token(self) -> tuple[str, str]:
        type(self).minted_write += 1
        return "ghs_app_write", "2099-01-01T00:00:00+00:00"

    async def readonly_token(self) -> tuple[str, str]:
        return "ghs_app_read", "2099-01-01T00:00:00+00:00"


@pytest.fixture
def app_world(monkeypatch):
    """一个接了平台 GitHub App、有 GitHub upstream 的项目 —— `ForgeKind.github_app`。

    返回一个 dict：`fake` 是假 GitHub（轮询那只），其余键记录本该产生副作用的
    调用，好让测试断言「本地合并一次都没发生」这类性质。
    """
    from app.core.config import settings
    from app.domain.agent import github_app
    from app.domain.review import pr_publish
    from app.domain.review.services import AcceptService
    from app.domain.workspace import service as ws

    fake = FakeGitHubPrClient()
    recorded: dict = {
        "fake": fake,
        "pushes": [],
        "repushes": [],
        "local_merges": [],
        "opened": [],
    }
    _FakeTokens.minted_write = 0

    class _AppPrOpener:
        """`pr_publish` 用来开 PR 的那只 client（App token，大写 PR 的那个）。
        开出来的 PR 同时登记进 `fake`，因为之后轮询读的是另一只 client。"""

        def __init__(self, owner: str, repo: str, tokens, **_):
            self.owner, self.repo = owner, repo

        async def open_pr(self, *, head: str, base: str, title: str, body: str) -> dict:
            number = 21 + len(recorded["opened"])
            recorded["opened"].append({"head": head, "base": base, "number": number})
            fake.seed_pr(number, head=head, base=base)
            return {
                "number": number,
                "html_url": f"https://github.com/{REPO}/pull/{number}",
            }

    monkeypatch.setattr(settings, "accept_via_pr", True)
    monkeypatch.setattr(settings, "github_app_id", 12345)
    monkeypatch.setattr(settings, "github_app_private_key_path", "/tmp/fake-app.pem")

    async def _tokens_for_project(_project_id, _session):
        return _FakeTokens()

    # 两个模块各自绑定了这个名字（accept 侧是懒导入，pr_publish 是模块级导入）。
    monkeypatch.setattr(
        github_app, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(
        pr_publish, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(pr_publish, "GitHubPRClient", _AppPrOpener)

    monkeypatch.setattr(ws, "get_upstream", lambda pid: f"https://github.com/{REPO}")
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid, tid: True)
    monkeypatch.setattr(ws, "ensure_repo", lambda pid: Path("."))
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo: "main")
    monkeypatch.setattr(ws, "pr_base_branch", lambda pid: "main")
    monkeypatch.setattr(ws, "sync_upstream", lambda pid: {"synced": True, "commits": 1})

    def _push(pid, tid, token):
        branch = ws.branch_for_topic(tid)
        recorded["pushes"].append({"topic": tid, "token": token, "branch": branch})
        return branch

    monkeypatch.setattr(ws, "push_topic_branch", _push)

    def _repush(pid, tid, *, owner, repo, remote_branch, token):
        recorded["repushes"].append({"remote_branch": remote_branch, "token": token})
        return {"head_sha": f"sha-{remote_branch}-2", "remote_branch": remote_branch}

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", _repush)

    def _local_merge(pid, tid):
        recorded["local_merges"].append(tid)
        return {"merged": False, "noop": True, "reason": "no topic branch"}

    monkeypatch.setattr(ws, "merge_topic", _local_merge)

    # 默认「工作区没有新提交」，重推机制与本文件绝大多数用例正交；专门测重推的
    # 那条自己覆盖回去。
    monkeypatch.setattr(
        AcceptService, "_local_topic_branch_head", lambda self, pid, tid: None
    )

    github_pr.set_default_client(fake)
    try:
        yield recorded
    finally:
        github_pr.set_default_client(None)


def _authorized(client, app_world) -> tuple[str, str, int, str]:
    """走到「人点了采纳、卡在 pr_open 等 CI」这一步。

    返回 (topic id, card id, PR 号, head sha)。
    """
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    card = r.json()["data"]
    assert card["status"] == "pr_open"
    number = card["pr_number"]
    return tid, cid, number, app_world["fake"].prs[number]["head_sha"]


# ---- 验收标准 1：检查没跑完时点采纳，合并 API 一次都不许被调用 ----------------


def test_accepting_while_checks_pend_authorizes_but_never_merges(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    # 这是这次改动的核心断言：人点了采纳，合并 API 一次都没被调用过。
    assert fake.merge_calls == []
    # 话题不归档、容器不停 —— 还在等 CI。
    assert _topic(client, tid)["status"] == "active"
    assert app_world["local_merges"] == []

    # 轮询器要的字段必须在这里就齐了：缺任何一个，advance_pr_card 只会 log 一行
    # error 然后 return，卡永远静默停在 pr_open。
    card = _cards(client, tid)[0]
    assert card["pr_repo"] == REPO
    assert card["pr_head_sha"] == head_sha
    assert card["pr_number"] == number
    # pr_authorized_sha 不在对外 schema 里，直接确认轮询能走下去即可（下一条）。


def test_a_pending_card_is_actually_pollable(client, app_world):
    """「字段齐了」不是靠读字段证明的，是靠轮询真的能推进它证明的。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")
    fake.status_calls.clear()
    _poll(client)
    card = _cards(client, tid)[0]
    assert card["status"] == "pr_open"
    # 轮询真的问过 GitHub 这个 PR（不是缺字段直接 return 了）。
    assert fake.status_calls == [number]


# ---- 验收标准 2：转绿后轮询自动合并、归档 -------------------------------------


def test_green_checks_merge_and_archive_on_the_next_poll(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("success", "全部 9 项检查通过")
    _poll(client)

    assert [m["number"] for m in fake.merge_calls] == [number]
    card = _cards(client, tid)[0]
    assert card["status"] == "accepted"
    assert _topic(client, tid)["status"] == "archived"
    assert app_world["local_merges"] == []


# ---- 验收标准 3：红的真的合不进去（最该钉死的一条）---------------------------


def test_red_checks_are_never_merged_no_matter_how_many_polls(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    for _ in range(3):
        _poll(client)
    wait_turns_idle()

    assert fake.merge_calls == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pr_open"
    assert _topic(client, tid)["status"] == "active"
    # 卡面说得出「为什么没合」。
    assert "Backend Test" in card["note"]
    # 房间里也叫了芝士，不是一张没人管的卡。
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    contents = "\n".join(b.get("content") or "" for b in blocks)
    assert "Backend Test" in contents


# ---- 验收标准 4：根本没有 CI 会跑这次改动 -------------------------------------


def test_no_checks_stops_short_of_merging_and_says_what_to_do(client, app_world):
    """「12 项全过」和「没有 workflow 会对这次改动触发检查」是两件事。后者不享受
    免人自动合并 —— 但也不能变成一张永远不动、没人知道该干嘛的卡。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("no_checks", "没有 workflow 匹配这次改动")

    _poll(client)

    assert fake.merge_calls == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pr_open"
    assert _topic(client, tid)["status"] == "active"
    # 卡面写清了「没有 CI 跑过这次改动」，而且给了人一条能走的路。
    assert "没有任何 CI" in card["note"]
    assert "人工放行" in card["note"]


# ---- 验收标准 5：等待期间卡面写明在等什么、等了多久，且不盖掉真失败 -----------


def test_waiting_card_says_what_it_waits_for(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test、E2E")

    _poll(client)

    note = _cards(client, tid)[0]["note"]
    assert note.startswith("⏳ 等 CI")
    assert "Backend Test" in note  # 在等什么
    assert "等" in note  # 等了多久（刚采纳 → 「刚开始等」）


def test_waiting_note_does_not_churn_on_every_tick(client, app_world):
    """轮询每 60 秒一次。等待提示必须是稳定的，不能每一轮都重写一遍。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")

    _poll(client)
    first = _cards(client, tid)[0]["note"]
    _poll(client)
    _poll(client)
    assert _cards(client, tid)[0]["note"] == first


def test_waiting_note_never_overwrites_a_real_failure(client, app_world):
    """CI 红了之后检查又回到 pending（重跑、或新增了一个 job），等待提示绝不能
    把「检查未通过」那条盖掉 —— 那条是要人动手的，等待提示不是。"""
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)

    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    _poll(client)
    wait_turns_idle()
    failed_note = _cards(client, tid)[0]["note"]
    assert failed_note.startswith("⚠️")

    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")
    _poll(client)
    assert _cards(client, tid)[0]["note"] == failed_note
    assert fake.merge_calls == []


# ---- 验收标准 6：署名的人工放行出口 ------------------------------------------


def _merge_anyway(
    client, card_id: str, handle: str | None = None, reason: str = "", **kw
):
    headers = dict(kw.pop("headers", {}))
    if handle is not None:
        headers.update(session_auth_headers(handle))
    return client.post(
        f"/api/accept-cards/{card_id}/merge-anyway",
        json={"reason": reason},
        headers=headers,
        **kw,
    )


def test_merge_anyway_is_for_humans_only(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")
    pid = _topic(client, tid)["project_id"]

    # 没登录：全局 sandbox token 仍在，证明它顶不了一个身份。
    saved = client.headers.pop("Authorization", None)
    assert _merge_anyway(client, cid).status_code == 401
    if saved is not None:
        client.headers["Authorization"] = saved

    # 芝士拿着作用域内的 token 也不行 —— 放行是授权类动作，只给人。
    r = _merge_anyway(
        client, cid, headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)}
    )
    assert r.status_code == 422, r.text
    assert "AI" in r.json()["message"]

    # 跟这张卡无关的人也不行。
    assert _merge_anyway(client, cid, "mallory").status_code == 403

    # 三次都没合。
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pr_open"


def test_merge_anyway_merges_and_signs_the_card(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    # 先证明机器自己不会合。
    _poll(client)
    wait_turns_idle()
    assert fake.merge_calls == []

    r = _merge_anyway(client, cid, "alice", reason="CI runner 挂了，跟这次改动无关")
    assert r.status_code == 200, r.text
    card = r.json()["data"]

    assert [m["number"] for m in fake.merge_calls] == [number]
    assert card["status"] == "accepted"
    assert _topic(client, tid)["status"] == "archived"
    # 署名：谁、理由、以及合并那一刻检查到底是什么状态。
    assert "alice" in card["note"]
    assert "CI runner 挂了" in card["note"]
    assert "failure" in card["note"]


def test_merge_anyway_is_refused_once_the_card_is_settled(client, app_world):
    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全部通过")
    _poll(client)
    assert _cards(client, tid)[0]["status"] == "accepted"

    assert _merge_anyway(client, cid, "alice").status_code == 422
    assert len(fake.merge_calls) == 1  # 没有第二次合并


# ---- 验收标准 7：轮询用的是 App 的 token，不是验收人的 -----------------------


def test_poll_advances_even_when_the_approver_has_no_github_account(
    client, app_world, monkeypatch
):
    """验收人本人没连 GitHub（也未必有 main 的写权限）时，这张已经授权过的卡
    照样推进并合并 —— 因为轮询用的是平台 App 的 write token。"""

    async def _never_connected(_session, _handle, **_kw):
        return None, "not_connected"

    monkeypatch.setattr(
        "app.domain.oauth.services.get_github_user_token_for_handle_with_reason",
        _never_connected,
    )

    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全部通过")

    _poll(client)

    assert [m["number"] for m in fake.merge_calls] == [number]
    assert _cards(client, tid)[0]["status"] == "accepted"
    assert _topic(client, tid)["status"] == "archived"
    # 用的确实是 App 的 token（accept 时一次 + 轮询时至少一次）。
    assert _FakeTokens.minted_write >= 2


# ---- 验收标准 8：重推推到这个 PR 真正的 head 分支 ----------------------------


def test_repush_targets_the_prs_own_head_branch(client, app_world, monkeypatch):
    """App 的 PR 开在 `topic/<hex8>` 上。以前轮询按话题 id 推算分支名，推的是
    `cheesex/<hex8>` —— 提交落了地，PR 一动不动。"""
    from app.domain.review.services import AcceptService

    fake = app_world["fake"]
    tid, cid, number, head_sha = _authorized(client, app_world)
    pr_branch = fake.prs[number]["head"]
    assert pr_branch.startswith("topic/")  # App 这条路的 PR 就是开在这里的

    # 芝士在工作区里改了东西：本地 head 动了，且能快进。
    monkeypatch.setattr(
        AcceptService,
        "_local_topic_branch_head",
        lambda self, pid, tid_: "local-head-after-fix",
    )
    monkeypatch.setattr(
        AcceptService,
        "_remote_head_ff_from_local",
        lambda self, pid, remote, local: True,
    )
    fake.check_state_by_sha[head_sha] = ("pending", "等待中：Backend Test")

    _poll(client)

    assert [p["remote_branch"] for p in app_world["repushes"]] == [pr_branch]
    # 而且用的是 App 的 token，不是某个人的。
    assert app_world["repushes"][0]["token"] == "ghs_app_write"


# ---- 现场补开 PR：失败停下、PR 记录不丢 --------------------------------------


def test_github_unreachable_at_accept_stops_and_keeps_the_pr_on_the_card(
    client, app_world
):
    """GitHub 读不到时采纳停下（可重试），绝不改走本地合并直推；已经开出来的
    PR 必须留在卡上，否则重试会再开一个。"""
    fake = app_world["fake"]
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    async def _explode(*_a, **_kw):
        raise github_pr.GitHubPrError("GitHub unreachable")

    # 实例属性遮住类方法。这里不用 monkeypatch：它跟 app_world 是同一个
    # function-scoped 实例，undo() 会把整个 App 世界一起拆掉。
    fake.pull_request_status = _explode

    r = _accept(client, cid)
    assert r.status_code == 422
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["note"].startswith("⚠️ 采纳未完成：PR 未能合并")
    assert card["pr_number"] == 21  # 事务外持久化，重试不会重开
    assert app_world["local_merges"] == []
    assert _topic(client, tid)["status"] == "active"

    # GitHub 回来了 → 同一张卡走同一个 PR 进 pr_open，没有开出第二个 PR。
    del fake.pull_request_status
    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "pr_open"
    assert len(app_world["opened"]) == 1
    assert fake.merge_calls == []


def test_pr_already_merged_on_github_is_taken_as_the_accept(
    client, app_world, monkeypatch
):
    """人自己在 GitHub 上把这个 PR 合了 —— 同一件事，照单收下，不再进 pr_open。"""
    from app.domain.review import pr_publish

    fake = app_world["fake"]

    class _PreMerged:
        def __init__(self, owner, repo, tokens, **_):
            pass

        async def open_pr(self, *, head, base, title, body):
            fake.seed_pr(21, head=head, base=base)
            fake.merge_externally(21)
            app_world["opened"].append({"head": head, "number": 21})
            return {"number": 21, "html_url": f"https://github.com/{REPO}/pull/21"}

    monkeypatch.setattr(pr_publish, "GitHubPRClient", _PreMerged)

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"
    assert _topic(client, tid)["status"] == "archived"
    assert fake.merge_calls == []  # 平台没有再合一次
    assert app_world["local_merges"] == []


def test_discussion_topic_needs_no_pr_and_still_accepts(client, app_world, monkeypatch):
    """绑定项目里的讨论型话题没有分支，没有能进 PR 的改动 —— 本地合并 no-op 完
    成采纳，什么都没被绕过。这条守着一件事：新的授权路不能把这类话题卡死。"""
    from app.domain.workspace import service as ws

    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid, tid: False)

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"
    assert _topic(client, tid)["status"] == "archived"
    assert app_world["opened"] == []
    assert app_world["fake"].merge_calls == []
