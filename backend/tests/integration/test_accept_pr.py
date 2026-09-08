"""采纳 = 当场调合并 API，合的是人看到的那个 commit（issue #718）。

这里钉的是**性质**：clean 当场合并且合并调用带着卡面显示的 head sha；head
漂移（点击前或点击瞬间）→ 卡刷新、按 dismiss_stale 清票，绝不合没人看过的
commit；规则没满足 → 拒绝采纳，合并 API 一次都没被调用；轮询器只做三件事
（镜像合并态 / 按表发事件 / 合 armed 的卡），自己绝不替人合未布防的卡。

GitHub 全程是 test double：开 PR 走 `pr_publish.GitHubPRClient`（App 那只），
点击与轮询走 `github_pr.default_client()`（协议那只）。分支保护规则按项目配置
（PUT /projects/{id}/branch-protection），本文件的用例各自声明自己要的规则。
"""

import asyncio
import subprocess
import uuid as _uuid
from datetime import UTC, datetime

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.review import github_pr
from tests.conftest import wait_work_idle
from tests.integration.conftest import room_text, session_auth_headers
from tests.machine_work import machine_commits

REPO = "acme/widgets"


def _make_project(client) -> str:
    r = client.post(
        "/projects", json={"name": "P"}, headers=session_auth_headers("alice")
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card_response(client, topic_id: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = _make_card_response(client, topic_id, reviewer)
    assert r.status_code == 200
    return r.json()["data"]["id"]


#: "whatever this card shows right now" —— 默认的点击是刚看过卡再点的那种。
#: 传一个具体的 sha 就是「浏览器里那张卡停在这一版」，TOCTOU 的用例靠它。
JUST_LOOKED = object()


def _rendered_head(client, card_id: str) -> str | None:
    """浏览器渲染这张卡时卡面上的 head（payload 的 `merge_state.head_sha`）。

    三个会触发合并的入口都要求请求声明它，所以测试的默认调用也照做——不然每个
    用例都在测「不带 sha 的老客户端」，而那条路本来就该被拒。"""
    from app.domain.review.repositories import AcceptCardRepository

    async def _do() -> str | None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None
            return card.pr_head_sha

    return asyncio.run(_do())


def _seen(client, card_id: str, head_sha) -> str | None:
    return _rendered_head(client, card_id) if head_sha is JUST_LOOKED else head_sha


def _accept(client, card_id: str, handle: str = "alice", *, head_sha=JUST_LOOKED):
    return client.post(
        f"/accept-cards/{card_id}/accept",
        json={
            "decided_by": handle,
            "head_sha": _seen(client, card_id, head_sha),
        },
        headers=session_auth_headers(handle),
    )


def _approve(client, card_id: str, handle: str):
    return client.post(
        f"/accept-cards/{card_id}/approve",
        json={"approver_handle": handle},
        headers=session_auth_headers(handle),
    )


def _merge_anyway(client, card_id: str, handle: str | None = None, **kw):
    headers = kw.pop("headers", None)
    if headers is None and handle is not None:
        headers = session_auth_headers(handle)
    return client.post(
        f"/accept-cards/{card_id}/merge-anyway",
        json={
            "reason": kw.pop("reason", ""),
            "head_sha": _seen(client, card_id, kw.pop("head_sha", JUST_LOOKED)),
        },
        headers=headers or {},
    )


def _arm(
    client,
    card_id: str,
    handle: str,
    *,
    enabled: bool = True,
    head_sha=JUST_LOOKED,
):
    return client.post(
        f"/accept-cards/{card_id}/auto-merge",
        json={
            "enabled": enabled,
            "head_sha": _seen(client, card_id, head_sha),
        },
        headers=session_auth_headers(handle),
    )


def _protect(client, pid: str, **body):
    r = client.put(
        f"/projects/{pid}/branch-protection",
        json=body,
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _cards(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/accept-card").json()["data"]["data"]


def _topic(client, topic_id: str) -> dict:
    return client.get(f"/topics/{topic_id}").json()["data"]


def _poll(client) -> dict:
    r = client.post("/admin/scheduler/poll-open-prs")
    assert r.status_code == 200
    return r.json()["data"]


def _room(client, topic_id: str) -> str:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return room_text(blocks)


def _room_settled(client, topic_id: str, needle: str, *, tries: int = 40) -> str:
    """房间通知是 fire-and-forget（spawn），给事件循环几拍落块的时间。"""
    import time

    text = _room(client, topic_id)
    for _ in range(tries):
        if needle in text:
            break
        time.sleep(0.05)
        text = _room(client, topic_id)
    return text


class FakeGitHubPrClient:
    """#718 的假 GitHub —— no real calls. State is plain dicts keyed by PR
    number / commit sha so a test can move it forward between polls.

    `mergeable_state` 默认从检查状态推导（和真 GitHub 一致：冲突 → dirty，
    有没过的检查 → unstable，其余 → clean），要透传别的词用
    `mergeable_state_by_number` 显式盖。"""

    def __init__(self) -> None:
        self._next_number = 100
        self.prs: dict[int, dict] = {}
        self.check_state_by_sha: dict[str, tuple[str, str]] = {}
        self.check_runs_by_sha: dict[str, list[dict]] = {}
        self.merge_sha_by_number: dict[int, str | None] = {}
        # number → GitHub's refusal reason (405); wins over a sha.
        self.merge_blocked_by_number: dict[int, str] = {}
        self.mergeable_state_by_number: dict[int, str] = {}
        self.mergeable_by_number: dict[int, bool | None] = {}
        self.draft_by_number: dict[int, bool] = {}
        self.files_by_sha: dict[str, list[tuple[str, str]] | None] = {}
        self.compare_calls: list[tuple[str, str]] = []
        self.compare_status_by_pair: dict[tuple[str, str], str] = {}
        self.compare_status_calls: list[tuple[str, str]] = []
        self.update_branch_calls: list[int] = []
        self.update_branch_result: bool = True
        self.update_branch_tokens: list[str] = []
        self.check_state_error: Exception | None = None
        self.status_error: Exception | None = None
        self.opened: list[dict] = []
        self.merge_calls: list[dict] = []
        self.status_calls: list[int] = []
        self.status_tokens: list[str] = []
        self.head_sha_calls: list[int] = []
        self.check_state_tokens: list[str] = []
        self.list_check_runs_tokens: list[str] = []
        self.reviews_by_number: dict[int, list] = {}
        self.review_signal_calls: list[tuple[int, bool]] = []

    # ---- test helpers ------------------------------------------------------

    def seed_pr(self, number: int, *, head: str, base: str = "main") -> str:
        """Register a PR (the App lane opens PRs through a different client, so
        the poller side has to be told it exists). Returns its head sha."""
        head_sha = f"sha-{head}-1"
        self.prs[number] = {
            "head": head,
            "base": base,
            "head_sha": head_sha,
            "state": "open",
            "merged": False,
            "merge_commit_sha": None,
            "merged_at": None,
        }
        return head_sha

    def merge_externally(
        self,
        number: int,
        *,
        merge_commit_sha: str | None = "human-merge-sha",
        merged_at: datetime | None = None,
    ) -> None:
        self.prs[number].update(
            state="closed",
            merged=True,
            merge_commit_sha=merge_commit_sha,
            merged_at=merged_at,
        )

    def close_unmerged(self, number: int) -> None:
        self.prs[number].update(state="closed", merged=False)

    def push_new_commit(self, number: int) -> str:
        """Simulate a fresh push — moves the PR's head."""
        new_sha = self.prs[number]["head_sha"] + "x"
        self.prs[number]["head_sha"] = new_sha
        return new_sha

    def _derived_mergeable_state(self, number: int) -> str:
        if number in self.mergeable_state_by_number:
            return self.mergeable_state_by_number[number]
        if self.mergeable_by_number.get(number) is False:
            return "dirty"
        state, _ = self.check_state_by_sha.get(
            self.prs[number]["head_sha"], ("absent", "")
        )
        return "unstable" if state in ("pending", "failure") else "clean"

    def _derived_check_runs(self, ref: str) -> list[dict]:
        if ref in self.check_runs_by_sha:
            return self.check_runs_by_sha[ref]
        state, _ = self.check_state_by_sha.get(ref, ("absent", ""))
        match state:
            case "success":
                return [
                    {"name": "test", "status": "completed", "conclusion": "success"}
                ]
            case "failure":
                return [
                    {"name": "test", "status": "completed", "conclusion": "failure"}
                ]
            case "pending":
                return [{"name": "test", "status": "in_progress", "conclusion": None}]
            case _:
                return []

    # ---- the protocol ------------------------------------------------------

    async def open_pull_request(
        self, *, owner, repo, head, base, title, body, token
    ) -> github_pr.PullRequest:
        self._next_number += 1
        number = self._next_number
        head_sha = self.seed_pr(number, head=head, base=base)
        self.opened.append({"head": head, "base": base, "title": title, "body": body})
        return github_pr.PullRequest(
            number=number,
            url=f"https://github.com/{owner}/{repo}/pull/{number}",
            head_sha=head_sha,
        )

    async def pull_request_head_sha(self, *, owner, repo, number, token) -> str:
        self.head_sha_calls.append(number)
        return self.prs[number]["head_sha"]

    async def pull_request_status(
        self, *, owner, repo, number, token
    ) -> github_pr.PullRequestStatus:
        if self.status_error is not None:
            raise self.status_error
        pr = self.prs[number]
        self.status_calls.append(number)
        self.status_tokens.append(token)
        return github_pr.PullRequestStatus(
            head_sha=pr["head_sha"],
            head_ref=pr["head"],
            state=pr["state"],
            merged=pr["merged"],
            merge_commit_sha=pr["merge_commit_sha"],
            merged_at=pr["merged_at"],
            mergeable=self.mergeable_by_number.get(number),
            mergeable_state=self._derived_mergeable_state(number),
            draft=self.draft_by_number.get(number, False),
            review_comment_count=sum(
                1 for s in self.reviews_by_number.get(number, []) if s.kind == "comment"
            ),
        )

    async def check_state(self, *, owner, repo, ref, token) -> tuple[str, str]:
        self.check_state_tokens.append(token)
        if self.check_state_error is not None:
            raise self.check_state_error
        return self.check_state_by_sha.get(ref, ("pending", "还没跑"))

    async def list_check_runs(self, *, owner, repo, ref, token) -> list[dict]:
        self.list_check_runs_tokens.append(token)
        if self.check_state_error is not None:
            raise self.check_state_error
        return self._derived_check_runs(ref)

    async def compare_files(
        self, *, owner, repo, base, head, token
    ) -> list[tuple[str, str]] | None:
        self.compare_calls.append((base, head))
        return self.files_by_sha.get(head, [])

    async def merge_pull_request(
        self,
        *,
        owner,
        repo,
        number,
        token,
        commit_title=None,
        commit_message=None,
        sha=None,
    ) -> github_pr.MergeResult:
        self.merge_calls.append(
            {
                "number": number,
                "commit_title": commit_title,
                "commit_message": commit_message,
                "token": token,
                "sha": sha,
            }
        )
        blocked = self.merge_blocked_by_number.get(number)
        if blocked is not None:
            return github_pr.MergeResult(blocked_reason=blocked)
        if sha is not None and sha != self.prs[number]["head_sha"]:
            # Real GitHub: the sha guard answers 409 when the head moved.
            return github_pr.MergeResult(
                blocked_reason="HTTP 409：Head branch was modified",
                stale_head=True,
            )
        merge_sha = self.merge_sha_by_number.get(number, "merge-sha-default")
        self.merge_externally(
            number, merge_commit_sha=merge_sha, merged_at=datetime.now(UTC)
        )
        return github_pr.MergeResult(sha=merge_sha)

    async def compare_status(self, *, owner, repo, base, head, token) -> str | None:
        self.compare_status_calls.append((base, head))
        return self.compare_status_by_pair.get((base, head))

    async def review_signals(
        self, *, owner, repo, number, token, with_comments=True
    ) -> list:
        self.review_signal_calls.append((number, with_comments))
        signals = list(self.reviews_by_number.get(number, []))
        if not with_comments:
            signals = [s for s in signals if s.kind != "comment"]
        return signals

    async def check_run_names(self, *, owner, repo, ref, token) -> set[str]:
        return {r["name"] for r in self._derived_check_runs(ref)}

    async def update_branch(self, *, owner, repo, number, token) -> bool:
        self.update_branch_calls.append(number)
        self.update_branch_tokens.append(token)
        if self.update_branch_result:
            # GitHub's Update branch merges base into head → the head moves.
            pr = self.prs[number]
            pr["head_sha"] = f"{pr['head_sha']}-rebased"
        return self.update_branch_result


class _FakeTokens:
    """平台 GitHub App 的 installation token。这条路上唯一该用的凭据。"""

    minted_write = 0
    minted_read = 0

    async def write_token(self) -> tuple[str, str]:
        type(self).minted_write += 1
        return "ghs_app_write", "2099-01-01T00:00:00+00:00"

    async def installation_token(self) -> tuple[str, str]:
        type(self).minted_read += 1
        return "ghs_app_read", "2099-01-01T00:00:00+00:00"


@pytest.fixture
def app_world(client, monkeypatch):
    """一个接了平台 GitHub App、有 GitHub upstream 的项目 —— `ForgeKind.github_app`。

    返回一个 dict：`fake` 是假 GitHub，其余键记录本该产生副作用的调用，好让测试
    断言「本地合并一次都没发生」这类性质。GitHub 自己没开保护（`_github_enforces`
    → False）：平台按项目配置补位，正是本仓库这类 free 计划私有仓的现实。
    """
    from app.domain.agent import github_app
    from app.domain.review import pr_publish
    from app.domain.review import services as review_services
    from app.domain.review.services import AcceptService
    from app.domain.workspace import service as ws

    fake = FakeGitHubPrClient()
    recorded: dict = {
        "fake": fake,
        "pushes": [],
        "repushes": [],
        "local_merges": [],
        "opened": [],
        "patched": [],
        "readied": [],
        "prs_by_head": {},
    }
    _FakeTokens.minted_write = 0
    _FakeTokens.minted_read = 0

    class _AppPrOpener:
        """`pr_publish` 用来开 PR 的那只 client（App token，大写 PR 的那个）。
        开出来的 PR 同时登记进 `fake`，因为之后点击/轮询读的是另一只 client。

        一条 head 上只有一个开着的 PR：同一条分支再开一次，拿回的是同一个
        （真 GitHub 是 422「already exists」+ 查回来，`open_pr` 内部做的）。
        `update_pr` / `mark_ready_for_review` 在这里是真的会改状态的，因为
        「认领来的 draft PR 会被改写文案并翻成 ready」正是递卡这一步的行为。"""

        def __init__(self, owner: str, repo: str, tokens, **_):
            self.owner, self.repo = owner, repo

        async def open_pr(
            self,
            *,
            head: str,
            base: str,
            title: str,
            body: str,
            as_user_token: str | None = None,
            draft: bool = False,
        ) -> dict:
            if head in recorded["prs_by_head"]:
                adopted = recorded["prs_by_head"][head]
                recorded["opened"].append(
                    {
                        "head": head,
                        "base": base,
                        "draft": draft,
                        "title": title,
                        "number": adopted["number"],
                        "adopted": True,
                    }
                )
                return adopted
            number = 21 + len(recorded["prs_by_head"])
            recorded["opened"].append(
                {
                    "head": head,
                    "base": base,
                    "draft": draft,
                    "title": title,
                    "number": number,
                    "adopted": False,
                }
            )
            fake.seed_pr(number, head=head, base=base)
            fake.draft_by_number[number] = draft
            pr = {
                "number": number,
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "title": title,
                "body": body,
                "draft": draft,
                "node_id": f"PR_node_{head}",
            }
            recorded["prs_by_head"][head] = pr
            return pr

        async def update_pr(self, number: int, *, title: str, body: str) -> dict:
            recorded["patched"].append(
                {"number": number, "title": title, "body": body}
            )
            for pr in recorded["prs_by_head"].values():
                if pr["number"] == number:
                    pr.update(title=title, body=body)
                    return pr
            return {"number": number, "title": title, "body": body}

        async def mark_ready_for_review(self, node_id: str) -> None:
            recorded["readied"].append(node_id)
            for pr in recorded["prs_by_head"].values():
                if pr.get("node_id") == node_id:
                    pr["draft"] = False
                    fake.draft_by_number[pr["number"]] = False

    async def _tokens_for_project(_project_id, _session):
        return _FakeTokens()

    monkeypatch.setattr(
        github_app, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(
        pr_publish, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(pr_publish, "GitHubPRClient", _AppPrOpener)
    # 递卡那一刻的 fire-and-forget 开 PR 在这里是噪音（竞态源）：默认关掉，
    # 「卡上有没有 PR」由每个测试自己决定（见 _give_card_a_pr）。
    monkeypatch.setattr(pr_publish, "dispatch", lambda *a, **kw: None)

    # GitHub 自己没开保护：平台补位。真实现走 HTTP，测试里必须钉死。
    async def _no_enforce(_repo, _token):
        return False

    monkeypatch.setattr(review_services, "_github_enforces", _no_enforce)
    # 房间通知（fire-and-forget 的 _notify_merge_result）写库走模块级
    # async_session_factory —— 测试 harness 把它绑在另一个库上，这里指回
    # 本测试的库，房间文本才断言得到。
    monkeypatch.setattr(review_services, "async_session_factory", client.test_factory)

    monkeypatch.setattr(ws, "get_upstream", lambda pid: f"https://github.com/{REPO}")
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid, tid: True)
    monkeypatch.setattr(ws, "upstream_default_branch", lambda repo, **_: "main")
    monkeypatch.setattr(ws, "pr_base_branch", lambda pid: "main")
    monkeypatch.setattr(
        ws, "sync_upstream", lambda pid, token=None: {"synced": True, "commits": 1}
    )

    def _push(pid, tid, token):
        branch = ws.branch_for_tree(tid)
        recorded["pushes"].append({"topic": tid, "token": token, "branch": branch})
        return branch

    monkeypatch.setattr(ws, "push_topic_branch", _push)

    def _repush(pid, tid, *, owner, repo, remote_branch, token):
        recorded["repushes"].append({"remote_branch": remote_branch, "token": token})
        return {"head_sha": f"sha-{remote_branch}-2", "remote_branch": remote_branch}

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", _repush)

    def _local_merge(pid, tid, **_kwargs):
        recorded["local_merges"].append(tid)
        return {"merged": True, "commit": "local-merge-sha"}

    monkeypatch.setattr(ws, "merge_topic", _local_merge)

    # 默认「工作区没有新提交」；专门测 push-fix 的用例自己覆盖回去。
    monkeypatch.setattr(
        AcceptService, "_local_topic_branch_head", lambda self, pid, tid: None
    )

    github_pr.set_default_client(fake)
    try:
        yield recorded
    finally:
        github_pr.set_default_client(None)


def _give_card_a_pr(
    client,
    app_world,
    topic_id: str,
    card_id: str,
    number: int = 7,
    *,
    mirrored: bool = True,
):
    """把卡做成「递卡时 PR 就已经开好了」的样子 —— 生产上的常态（`pr_publish`
    在递卡时 fire-and-forget 开 PR）。返回 PR 的 head sha。

    `mirrored=True` 是轮询器已经跑过一跳、head 写在卡上、卡面因此显示得出一个
    sha 的常态。`mirrored=False` 是刚递上来的那 60 秒：PR 有了，卡面还是空的，
    人没有任何一版可看。"""
    branch = f"topic/{_uuid.UUID(topic_id).hex[:8]}"
    head_sha = app_world["fake"].seed_pr(number, head=branch)

    from app.domain.review.repositories import AcceptCardRepository

    async def _do() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None
            card.pr_number = number
            card.pr_url = f"https://github.com/{REPO}/pull/{number}"
            if mirrored:
                card.pr_head_sha = head_sha
            await s.commit()

    asyncio.run(_do())
    return head_sha


def _ready_card(
    client,
    app_world,
    *,
    reviewer: str = "alice",
    number: int = 7,
    mirrored: bool = True,
) -> tuple[str, str, str, int, str]:
    """(pid, tid, cid, number, head_sha): a pending card riding a PR."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, reviewer)
    head_sha = _give_card_a_pr(client, app_world, tid, cid, number, mirrored=mirrored)
    return pid, tid, cid, number, head_sha


def _set_merge_since(client, card_id: str, iso: str) -> None:
    """Rewind the mirror's `since` clock — the grace timer's input."""
    from app.domain.review.repositories import AcceptCardRepository

    async def _do() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None and isinstance(card.merge_state, dict)
            card.merge_state = {**card.merge_state, "since": iso}
            await s.commit()

    asyncio.run(_do())


# ============================ 点击 = 当场合并 ================================


def test_accept_merges_the_pr_on_the_spot_when_clean(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全部通过")
    fake.merge_sha_by_number[number] = "merge-sha-1"

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    card = r.json()["data"]

    assert card["status"] == "accepted"
    assert card["pr_merged_at"] is not None
    # 合的是人看到的那个 commit：merge API 带着卡面显示的 head sha。
    assert [m["sha"] for m in fake.merge_calls] == [head_sha]
    # 合并用 App 的 write mint，不是谁的个人 token。
    assert fake.merge_calls[0]["token"] == "ghs_app_write"
    # 递卡时写的 subject 就是落进历史的那一行（`(#N)` 是显式补的）。
    assert (
        fake.merge_calls[0]["commit_title"]
        == f"chore(test): file an accept card (#{number})"
    )
    assert "Reviewed-by: alice" in fake.merge_calls[0]["commit_message"]
    # 交付完成 ≠ 话题结束 (#442 decision 1)。
    delivered = _topic(client, tid)
    assert delivered["status"] == "active"
    assert delivered["accepted_at"] is not None
    assert app_world["local_merges"] == []


def test_accept_is_refused_while_a_required_check_is_red(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.check_state_by_sha[head_sha] = ("failure", "pytest: 3 failed")

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "不能采纳" in r.json()["message"]
    assert "test" in r.json()["message"]
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"


def test_accept_is_refused_while_a_required_check_has_not_reported(client, app_world):
    """缺席是 pending，不是通过（#465/#468）——必跑名单现在是项目配置。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.check_runs_by_sha[head_sha] = []  # 什么检查都还没报到

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "没报到" in r.json()["message"] or "test" in r.json()["message"]
    assert fake.merge_calls == []


def _running(name: str) -> dict:
    return {"name": name, "status": "in_progress", "conclusion": None}


def _passed(name: str) -> dict:
    return {"name": name, "status": "completed", "conclusion": "success"}


def test_accept_is_refused_while_a_required_check_is_still_running(client, app_world):
    """洞②：必跑检查**还在跑**时点采纳，必须被拒。

    在跑的检查没有结论，而没有结论不是通过——这跟「必跑检查缺席」是同一句判词
    （#465/#468）。判成 unstable 时它会落进采纳闸门的放行集合
    `("clean", "unstable")`，于是配了必跑检查、test 还 in_progress 的 PR 照样
    合得掉：绿勾替一段还没被跑过的代码背了书。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test", "paths": ["backend/**"]}])
    fake.files_by_sha[head_sha] = [("modified", "backend/app/main.py")]
    fake.check_runs_by_sha[head_sha] = [_running("test")]

    r = _accept(client, cid)

    assert r.status_code == 422, r.text
    assert "不能采纳" in r.json()["message"]
    assert "test" in r.json()["message"]
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"

    # 跑完并且绿了，同一次点击照常合。
    fake.check_runs_by_sha[head_sha] = [_passed("test")]
    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert [m["sha"] for m in fake.merge_calls] == [head_sha]


def test_an_armed_card_waits_out_a_running_required_check(client, app_world):
    """洞②的轮询这条路，以及**布防与执行是两件事**（#718）：

    必跑检查在跑 = blocked，而「通过后自动合并」这个开关本来就只在 BLOCKED /
    BEHIND 出现——所以 blocked 不能拒绝布防。要挡住的是执行侧：布防之后、检查
    通过之前，轮询器一次都不许调 merge。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, auto_merge_allowed=True, required_checks=[{"name": "test"}])
    fake.check_runs_by_sha[head_sha] = [_running("test")]
    _poll(client)
    assert _cards(client, tid)[0]["merge_state"]["state"] == "blocked"

    assert _arm(client, cid, "alice").status_code == 200  # blocked 照样能布防

    _poll(client)
    _poll(client)
    assert fake.merge_calls == []  # 没绿，一次都不合
    assert _cards(client, tid)[0]["status"] == "pending"

    fake.check_runs_by_sha[head_sha] = [_passed("test")]
    _poll(client)

    card = _cards(client, tid)[0]
    assert card["status"] == "accepted"
    assert card["decided_by"] == "alice"
    assert [m["sha"] for m in fake.merge_calls] == [head_sha]


def test_an_unlisted_red_check_does_not_block_the_accept(client, app_world):
    """UNSTABLE（红的不在必跑名单，或名单为空）像 GitHub 一样可合 —— 名单不再是
    平台默认值（#640：要求被托管仓库先加我们点名的检查才配被采纳，是被否掉的）。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "style: 1 failed")

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"
    assert [m["number"] for m in fake.merge_calls] == [number]


def test_a_scoped_required_check_the_diff_cannot_trigger_is_not_required(
    client, app_world
):
    """带路径域的名单项（#470）：纯前端改动上 `test:backend/**` 缺席是正常，
    不是「还没跑」。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test", "paths": ["backend/**"]}])
    fake.check_runs_by_sha[head_sha] = []
    fake.files_by_sha[head_sha] = [("modified", "frontend/src/App.vue")]

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"


# ------------- 合的是人看到的那个 commit：轮询器在渲染与点击之间刷了卡 -------
#
# `_refresh_stale_card` 那条（下面几个用例）管的是「点击时现读 GitHub 发现 head
# 变了」。这一节管的是更早也更常见的一种：**轮询器已经把卡刷到新 head 了**，
# 数据库里干干净净，只有浏览器里那一份还停在旧版本。旧路径拿数据库里的 head 当
# merge 参数，于是这条路上合进去的是验收人从没看过的代码，而 dismiss_stale 拦
# 不住它——那条只清批准票，采纳本身就是一票。


def _stale_click_setup(client, app_world, *, number: int = 7):
    """(tid, cid, seen, live)：卡面镜像过 A，轮询器又把它刷成了 B。

    `seen` 是浏览器渲染那一刻卡上的 head（A），`live` 是卡现在的 head（B）。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world, number=number)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)
    seen = _cards(client, tid)[0]["merge_state"]["head_sha"]
    assert seen == head_sha  # 这就是人在屏幕上看到的那一版

    live = fake.push_new_commit(number)  # 芝士又推了
    fake.check_state_by_sha[live] = ("success", "新的也全绿")
    _poll(client)  # 轮询器把卡刷成新 head
    assert _cards(client, tid)[0]["pr_head_sha"] == live
    return pid, tid, cid, number, seen, live


def test_accepting_the_version_you_looked_at_refuses_once_the_card_moved_on(
    client, app_world
):
    """洞①（andylizf 独立复现）：读 A → 轮询刷成 B → 带着 A 点采纳。

    旧路径只收 decided_by，合并 API 拿的是**数据库里的** B —— 用户点的是「我看
    过的这一版可以」，落地的却是一段他没看过的代码。"""
    fake = app_world["fake"]
    _pid, tid, cid, _number, seen, live = _stale_click_setup(client, app_world)

    r = _accept(client, cid, head_sha=seen)

    assert r.status_code == 422, r.text
    assert "过时" in r.json()["message"]
    assert fake.merge_calls == []  # B 一次都没被合
    assert _cards(client, tid)[0]["status"] == "pending"

    # 重新看过（拿到 B）再点，才合，而且合的就是 B。
    assert _accept(client, cid).status_code == 200
    assert [m["sha"] for m in fake.merge_calls] == [live]


def test_force_merging_the_version_you_looked_at_refuses_once_the_card_moved_on(
    client, app_world
):
    """洞①的人工放行入口：签字的人要为**一段具体的代码**背书。屏幕上那一版
    已经不在了的时候，这个签名会落到别的东西上。"""
    fake = app_world["fake"]
    pid, tid, cid, _number, seen, live = _stale_click_setup(client, app_world)
    _protect(client, pid, required_checks=[{"name": "nope"}])  # 正门关着

    r = _merge_anyway(client, cid, "alice", reason="CI 挂了", head_sha=seen)

    assert r.status_code == 422, r.text
    assert "过时" in r.json()["message"]
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"

    r = _merge_anyway(client, cid, "alice", reason="CI 挂了")
    assert r.status_code == 200, r.text
    assert [m["sha"] for m in fake.merge_calls] == [live]


def test_arming_auto_merge_on_the_version_you_looked_at_refuses_too(client, app_world):
    """洞①的布防入口：布防就是提前采纳，替一段没人看过的代码预先按同意，跟
    当场合并它是同一件事。

    拒的理由只有「旧 SHA」一个 —— 拿着**当前**那一版布防照样成立，哪怕合并态
    正卡在 blocked（这个开关本来就只在 BLOCKED / BEHIND 出现）。"""
    pid, tid, cid, _number, seen, _live = _stale_click_setup(client, app_world)
    _protect(client, pid, auto_merge_allowed=True, required_checks=[{"name": "nope"}])
    _poll(client)  # 让卡面照新规则重算：必跑检查没报到 → blocked
    assert _cards(client, tid)[0]["merge_state"]["state"] == "blocked"

    r = _arm(client, cid, "alice", head_sha=seen)

    assert r.status_code == 422, r.text
    assert "过时" in r.json()["message"]
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] is None

    assert _arm(client, cid, "alice").status_code == 200
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] == "alice"


def test_disarming_never_needs_a_fresh_look(client, app_world):
    """解除布防不声明看过哪一版也行：撤销自己的同意什么都不会合并，为它加一道
    「先重新看过」的闸，只会让人被自己的旧布防困住。"""
    pid, tid, cid, _number, seen, _live = _stale_click_setup(client, app_world)
    _protect(client, pid, auto_merge_allowed=True)
    assert _arm(client, cid, "alice").status_code == 200

    r = _arm(client, cid, "alice", enabled=False, head_sha=seen)

    assert r.status_code == 200, r.text
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] is None


# ---- 卡面从没显示过任何版本（刚递上来的 PR 卡） ---------------------------
#
# 上面那一节的前提是「卡面显示过某个 sha」。刚递上来的卡还没有：轮询器 60s 才跑
# 一跳，`pr_head_sha` 在那之前一直是空的，前端因此送上来一个空的 head。
# 「卡上没有 sha」在 PR 这条 lane 上不是「没有版本可以过时」，而是**还不知道要合
# 什么** —— 放行等于拿现读 GitHub 的 head 去合一个从未在任何界面上出现过的
# commit。三个入口都是这样，所以三个入口都要先把 head 镜像上卡、让人重新看。


def _never_mirrored(client, app_world, *, number: int = 7):
    """(pid, tid, cid, number, live)：PR 开好了，轮询器一跳都还没跑过。"""
    fake = app_world["fake"]
    pid, tid, cid, number, live = _ready_card(
        client, app_world, number=number, mirrored=False
    )
    fake.check_state_by_sha[live] = ("success", "全绿")
    assert _cards(client, tid)[0]["pr_head_sha"] is None  # 卡面上没有任何一版
    assert _cards(client, tid)[0]["merge_state"]["head_sha"] is None
    return pid, tid, cid, number, live


def test_accepting_a_card_that_never_showed_a_version_refreshes_instead(
    client, app_world
):
    """刚递的卡上点采纳：不合，把当前 head 镜像上卡并要求重看。

    合下去的会是 GitHub 现在的 head，而它从来没有在验收人的屏幕上出现过 ——
    「我看过的这一版可以」这句话根本没有主语。"""
    fake = app_world["fake"]
    _pid, tid, cid, _number, live = _never_mirrored(client, app_world)

    r = _accept(client, cid)  # 卡面没 sha ⇒ 请求也带不出 sha

    assert r.status_code == 422, r.text
    assert fake.merge_calls == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_head_sha"] == live  # 卡已经刷新到当前 head
    assert "重新看过" in r.json()["message"]

    # 重新看过（这下卡面有 sha 了）再点，才合，合的就是那一版。
    assert _accept(client, cid).status_code == 200
    assert [m["sha"] for m in fake.merge_calls] == [live]


def test_force_merging_a_card_that_never_showed_a_version_refreshes_instead(
    client, app_world
):
    """人工放行的同一个洞：签字的人手上得先有一段具体的代码。"""
    fake = app_world["fake"]
    pid, tid, cid, _number, live = _never_mirrored(client, app_world)
    _protect(client, pid, required_checks=[{"name": "nope"}])  # 正门关着

    r = _merge_anyway(client, cid, "alice", reason="CI 挂了")

    assert r.status_code == 422, r.text
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["pr_head_sha"] == live
    assert "重新看过" in r.json()["message"]

    r = _merge_anyway(client, cid, "alice", reason="CI 挂了")
    assert r.status_code == 200, r.text
    assert [m["sha"] for m in fake.merge_calls] == [live]


def test_arming_auto_merge_on_a_card_that_never_showed_a_version_refreshes_instead(
    client, app_world
):
    """布防的同一个洞：布防是提前采纳，机器晚一点合的还是这个没人看过的
    commit。"""
    pid, tid, cid, _number, live = _never_mirrored(client, app_world)
    _protect(client, pid, auto_merge_allowed=True, required_checks=[{"name": "nope"}])

    r = _arm(client, cid, "alice")

    assert r.status_code == 422, r.text
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] is None
    assert _cards(client, tid)[0]["pr_head_sha"] == live
    assert "重新看过" in r.json()["message"]

    assert _arm(client, cid, "alice").status_code == 200
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] == "alice"


def test_a_pr_opened_at_accept_time_is_kept_but_not_merged_this_click(
    client, app_world
):
    """卡还没有 PR、采纳时才现开的那条路（fire-and-forget 失败或还在飞），走的是
    同一条约束。

    「人看的是同一条分支」不等于「同一个 commit」：浏览器从来没有声明过它渲染的
    diff 是哪个 sha，而分身边干边推是常态，所以现开的 PR 的 head 照样可能是没人
    看过的那个。开 PR 本身留着（有价值的副作用，下次采纳就有 head 可比），这一次
    不合。"""
    fake = app_world["fake"]
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    assert _cards(client, tid)[0]["pr_number"] is None

    r = _accept(client, cid)

    assert r.status_code == 422, r.text
    assert "重新看过" in r.json()["message"]
    assert fake.merge_calls == []  # GitHub 上一次都没合
    assert app_world["local_merges"] == []  # 也没有绕开 PR 本地合
    assert len(app_world["opened"]) == 1  # PR 开出来了，而且留着
    number = app_world["opened"][0]["number"]
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_number"] == number
    assert card["pr_head_sha"] == fake.prs[number]["head_sha"]

    # 重新看过（卡面这下有 sha 了）再点，才合，合的就是那一版。
    fake.check_state_by_sha[card["pr_head_sha"]] = ("success", "全绿")
    assert _accept(client, cid).status_code == 200, "重看之后应当能采纳"
    assert [m["sha"] for m in fake.merge_calls] == [card["pr_head_sha"]]
    assert len(app_world["opened"]) == 1  # 没有第二个 PR


def test_head_moved_since_the_reviewer_looked_refreshes_instead_of_merging(
    client, app_world
):
    """新提交作废已有的采纳（dismiss_stale 默认开）：点击时发现 head 已经不是
    卡面那个 → 不合并，卡刷新，票清空，人重新看。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)  # 镜像 head 到卡面 —— 这是「人看到的版本」
    assert _cards(client, tid)[0]["pr_head_sha"] == head_sha
    _approve(client, cid, "bob")
    assert _cards(client, tid)[0]["approvals"] == ["bob"]

    new_sha = fake.push_new_commit(number)  # 芝士又推了

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "重新看" in r.json()["message"]
    assert fake.merge_calls == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_head_sha"] == new_sha  # 卡已刷新到新 head
    assert card["approvals"] == []  # dismiss_stale：旧票作废
    assert "过时" in card["note"]


def test_a_409_from_github_refreshes_the_card_too(client, app_world):
    """点击瞬间的漂移由 GitHub 的 sha 参数兜住：merge API 409 → 同样刷新。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)

    # 状态读完之后、merge 调用之前，head 动了 —— 用 fake 的钩子拟合这个竞态：
    # merge 带的 sha 与「此刻」的 head 不一致，GitHub 409。
    real_status = fake.pull_request_status

    async def status_then_push(**kw):
        status = await real_status(**kw)
        if not fake.merge_calls:  # 只在点击那一次之后推
            fake.push_new_commit(number)
        return status

    fake.pull_request_status = status_then_push  # type: ignore[method-assign]

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "409" in r.json()["message"] or "刷新" in r.json()["message"]
    assert len(fake.merge_calls) == 1  # 调了，但被 GitHub 拦下
    assert _cards(client, tid)[0]["status"] == "pending"


def test_dismiss_stale_off_keeps_the_votes_on_a_moved_head(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, dismiss_stale=False)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)
    _approve(client, cid, "bob")

    fake.push_new_commit(number)
    _poll(client)

    assert _cards(client, tid)[0]["approvals"] == ["bob"]


def test_click_405_surfaces_githubs_reason_and_stops(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.merge_blocked_by_number[number] = (
        "HTTP 405：Merge commits are not allowed on this repository"
    )

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "Merge commits are not allowed" in r.json()["message"]
    assert _cards(client, tid)[0]["status"] == "pending"
    assert _topic(client, tid)["accepted_at"] is None


def _strip_delivery_claim(client, card_id: str) -> None:
    """把卡还原成 change_subject 出现之前递的存量卡（真正的纯讨论卡）。现在的
    递卡路径必填 subject，所以只能落库后抹掉。"""
    from app.domain.review.repositories import AcceptCardRepository

    async def _do() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None
            card.change_subject = None
            await s.commit()

    asyncio.run(_do())


def _branchless_noop_merge(app_world, monkeypatch) -> None:
    """树的分支不存在时 `ws.merge_topic` 真实的返回值——旧路径正是把这个 no-op
    当成功吞掉的（2026-09-07 卡 40be3e1a）。"""
    from app.domain.workspace import service as ws

    def _noop(pid, tid, **_kwargs):
        app_world["local_merges"].append(tid)
        return {"merged": False, "noop": True, "reason": "no topic branch"}

    monkeypatch.setattr(ws, "merge_topic", _noop)


def test_a_delivery_claim_on_a_branchless_tree_stops_the_accept(
    client, app_world, monkeypatch
):
    """回归（2026-09-07 卡 40be3e1a）：绑定项目 + 树分支缺失 + 卡带交付主张。
    改动被推到了别的分支，树的分支从未存在——开不出 PR，本地合并是 no-op，
    旧路径把卡标成 accepted，而改动没有合进任何地方。现在必须停下、原因落卡。"""
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)  # 递卡时分支还在（app_world 默认 True）
    # 采纳时分支没了/从未推上树：改动在别的分支上。
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid_, tid_: False)
    _branchless_noop_merge(app_world, monkeypatch)

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "没有任何提交" in r.json()["message"]

    card = _cards(client, tid)[0]
    assert card["status"] == "pending"  # 不落 accepted
    assert "没有任何提交" in card["note"]  # 原因写在卡上
    assert card["note_level"] == "error"
    assert app_world["opened"] == []  # 没开 PR
    assert app_world["fake"].merge_calls == []  # 没合 PR
    assert app_world["local_merges"] == []  # 本地合并一次都没发生
    assert _topic(client, tid)["accepted_at"] is None


def test_a_legacy_discussion_card_still_accepts_on_a_branchless_tree(
    client, app_world, monkeypatch
):
    """真正的纯讨论卡（存量、无 change_subject）不误伤：没有交付主张，no-op
    本地合并什么都没绕过，采纳照常完成。"""
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _strip_delivery_claim(client, cid)
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid_, tid_: False)
    _branchless_noop_merge(app_world, monkeypatch)

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"
    assert app_world["fake"].merge_calls == []
    assert app_world["opened"] == []
    assert app_world["local_merges"] == [_uuid.UUID(tid)]  # noop merge，无绕过


def test_filing_a_card_on_a_branchless_tree_is_refused(client, app_world, monkeypatch):
    """有活才有卡：绑定项目上树的分支不存在时，递卡当场被拒，错误信息点名
    该推哪条分支——而不是等到采纳时才发现无从交付。"""
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    monkeypatch.setattr(ws, "topic_branch_exists", lambda pid_, tid_: False)

    r = _make_card_response(client, tid)
    assert r.status_code == 422, r.text
    assert "没有任何提交" in r.json()["message"]
    # 说清该推哪条分支（本话题的树分支）。
    assert f"topic/{_uuid.UUID(tid).hex[:8]}" in r.json()["message"]
    assert _cards(client, tid) == []


# ---- 空树拒卡：报出来的分支名必须是下一次还找得到的那条 ---------------------
#
# 递卡在没有开着的树时会现场开一批新的，然后空树守卫可能把这次递卡拒掉。开一批
# 活落在两个地方：`work_trees` 的行（可回滚）和磁盘上「这个房间写哪棵树」的映射
# （不可回滚）。422 只回滚数据库，房间于是指着一棵不存在的树，而下一次递卡又开
# 一棵新的、报出**另一个**分支名——照着推永远白推（房间 2026-09-08 实测）。


def _named_branch(message: str) -> str:
    """错误信息里点名的那条分支。"""
    import re

    found = re.search(r"topic/[0-9a-f]{8}", message)
    assert found is not None, message
    return found.group(0)


def _room_open_tree_branch(client, topic_id: str) -> str | None:
    """数据库这一层说的「这个房间正在写哪棵树」，翻成分支名。"""
    from app.domain.room_task.repositories import WorkTreeRepository
    from app.domain.workspace import service as ws

    async def _do() -> str | None:
        async with client.test_factory() as s:
            tree = await WorkTreeRepository(s).open_tree_for_room(_uuid.UUID(topic_id))
            return None if tree is None else ws.branch_for_tree(tree.id)

    return asyncio.run(_do())


def _branch_of_record(client, topic_id: str) -> str:
    """磁盘这一层说的同一件事（`tree_for_place` → 分支名）。两层必须一致。"""
    from app.domain.workspace import service as ws

    return ws.branch_for_tree(ws.tree_for_place(_uuid.UUID(topic_id)))


def _only_these_branches_exist(monkeypatch, pushed: set[str]) -> None:
    """「分支存在」= 有人往它上面推过东西。芝士推一条就往 `pushed` 里加一条。"""
    from app.domain.workspace import service as ws

    def _exists(_project_id, topic_id) -> bool:
        return ws.branch_for_tree(ws.tree_for_place(topic_id)) in pushed

    monkeypatch.setattr(ws, "topic_branch_exists", _exists)


def test_a_delivery_after_the_last_batch_merged_keeps_the_tree_it_named(
    client, app_world, monkeypatch
):
    """房间 2026-09-08 实测的那一刀：上一批已经合并（房间没有开着的树）→ 再递
    卡时现场开新一批 → 空树守卫 422 → 回滚把刚开的那棵树一起烧掉。

    钉的是两层一致：拒卡之后，数据库里那棵开着的树、磁盘上的房间映射、错误信息
    里点名的分支，说的必须是同一条。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    assert _accept(client, cid).status_code == 200  # 上一批落地，树 merged
    # 合完当场就开了下一批：房间必须立刻有个别的地方可写，否则在下一次递卡之前
    # 提交的每一行都落在刚被 squash 进 main 的那条分支上。
    rolled = _room_open_tree_branch(client, tid)
    assert rolled is not None
    assert rolled != f"topic/{_uuid.UUID(tid).hex[:8]}"

    pushed: set[str] = set()  # 新一批的分支上还什么都没有
    _only_these_branches_exist(monkeypatch, pushed)

    r = _make_card_response(client, tid)

    assert r.status_code == 422, r.text
    assert "没有任何提交" in r.json()["message"]
    named = _named_branch(r.json()["message"])
    # 被拒之后，那棵树还在，而且三层说的是同一条分支。
    assert _room_open_tree_branch(client, tid) == named
    assert _branch_of_record(client, tid) == named
    # 而且它确实是新一批，不是上一批那条。
    assert named != f"topic/{_uuid.UUID(tid).hex[:8]}"

    pushed.add(named)  # 照着报的分支推提交
    r = _make_card_response(client, tid)
    assert r.status_code == 200, r.text


def test_two_refused_cards_name_the_same_branch_and_pushing_to_it_works(
    client, app_world, monkeypatch
):
    """连续两次空树拒卡，报的分支名必须一致——不然「把提交推上 X 后再递卡」
    这句指引本身就是假的。照着它推完，第三次递卡成功。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    assert _accept(client, cid).status_code == 200

    pushed: set[str] = set()
    _only_these_branches_exist(monkeypatch, pushed)

    first = _make_card_response(client, tid)
    second = _make_card_response(client, tid)

    assert first.status_code == 422 and second.status_code == 422
    named = _named_branch(first.json()["message"])
    assert _named_branch(second.json()["message"]) == named

    pushed.add(named)
    third = _make_card_response(client, tid)
    assert third.status_code == 200, third.text
    assert _cards(client, tid)[0]["status"] == "pending"


def test_github_unreachable_at_accept_stops_and_keeps_the_pr_on_the_card(
    client, app_world
):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.status_error = github_pr.GitHubPrError("GitHub 拒绝查 PR 状态（HTTP 502）")

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "采纳未完成" in r.json()["message"]
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_number"] == number  # PR 还挂在卡上，处理后可重试
    assert app_world["local_merges"] == []  # 绑定项目绝不落本地合并


def test_a_closed_unmerged_pr_stops_the_accept(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.close_unmerged(number)

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "关闭" in r.json()["message"]
    assert fake.merge_calls == []
    assert app_world["local_merges"] == []


def test_a_pr_already_merged_on_github_is_taken_as_the_accept(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.merge_externally(
        number,
        merge_commit_sha="a34b8e12",
        merged_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC),
    )

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "人工合并" in card["note"]
    assert fake.merge_calls == []  # 没有第二次合并


def test_accept_without_github_binding_local_merges(client):
    """未绑定项目 (#363)：本地合并就是它唯一、正当的采纳，如实标注。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert card["pr_number"] is None
    assert "本项目未接 GitHub" in card["note"]
    delivered = _topic(client, tid)
    assert delivered["status"] == "active"
    assert delivered["accepted_at"] is not None


# ============================ 轮询器的三件事 =================================


def test_poll_mirrors_the_merge_state_onto_the_card(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "还在跑")

    _poll(client)
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_repo"] == REPO  # 轮询把 repo 补到卡上
    assert card["pr_head_sha"] == head_sha
    mirror = card["merge_state"]
    assert mirror["state"] == "unstable"
    assert mirror["who"] == "ci"
    assert mirror["head_sha"] == head_sha

    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)
    mirror = _cards(client, tid)[0]["merge_state"]
    assert mirror["state"] == "clean"
    assert mirror["who"] == "human"


def test_poll_never_merges_an_unarmed_card(client, app_world):
    """采纳是人的点击；轮询器自己绝不替人合未布防的卡 —— #414 的反面。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    _poll(client)
    _poll(client)

    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"


def test_poll_clean_notifies_the_reviewer_once_per_head(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    _poll(client)
    text = _room_settled(client, tid, "等 alice 采纳")
    assert "等 alice 采纳" in text
    first = text.count("等 alice 采纳")

    _poll(client)  # 同一个 head：不重复
    wait_work_idle()
    assert _room(client, tid).count("等 alice 采纳") == first

    new_sha = fake.push_new_commit(number)  # 新 head 转绿是新事实
    fake.check_state_by_sha[new_sha] = ("success", "全绿")
    _poll(client)
    _poll(client)
    import time

    text = _room(client, tid)
    for _ in range(40):
        if text.count("等 alice 采纳") >= first + 1:
            break
        time.sleep(0.05)
        text = _room(client, tid)
    assert text.count("等 alice 采纳") == first + 1


def test_poll_red_checks_nudge_cheese_once_with_the_logs(client, app_world, stub_hooks):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "pytest: 3 failed")

    _poll(client)
    wait_work_idle()
    contents = _room(client, tid)
    assert "pytest: 3 failed" in contents
    prompt = stub_hooks.last_prompt or ""
    assert "cheese gh-token" in prompt
    assert f"repos/{REPO}/actions/jobs/" in prompt
    nudge_count = contents.count("pytest: 3 failed")

    _poll(client)  # 同一个失败：不重复
    wait_work_idle()
    assert _room(client, tid).count("pytest: 3 failed") == nudge_count

    # 新提交上同样的失败是新事实。
    new_sha = fake.push_new_commit(number)
    fake.check_state_by_sha[new_sha] = ("failure", "pytest: 1 failed now")
    _poll(client)
    wait_work_idle()
    assert "pytest: 1 failed now" in _room(client, tid)


def test_a_new_commit_dismisses_approvals_and_the_reviewer_is_told(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)
    _approve(client, cid, "bob")

    fake.push_new_commit(number)
    _poll(client)

    card = _cards(client, tid)[0]
    assert card["approvals"] == []
    assert "作废" in _room_settled(client, tid, "作废")

    # 没有可作废的东西时，head 移动不打扰任何人。
    before = _room(client, tid).count("作废")
    fake.push_new_commit(number)
    _poll(client)
    wait_work_idle()
    assert _room(client, tid).count("作废") == before


def test_behind_base_gets_updated_not_merged(client, app_world):
    """BEHIND 只在 strict 开时出现，是平台的活：GitHub 的 Update branch。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, strict=True)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.compare_status_by_pair[("main", head_sha)] = "behind"

    _poll(client)

    assert fake.update_branch_calls == [number]
    # Update branch 是推送，必须用 write mint（PR #575/#582 冻在 read 上过）。
    assert fake.update_branch_tokens == ["ghs_app_write"]
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"


def test_rebasing_stops_after_three_tries(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, strict=True)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.compare_status_by_pair[("main", head_sha)] = "behind"
    fake.update_branch_result = False  # 换基一直没生效，head 不动

    for _ in range(3):
        _poll(client)
    assert len(fake.update_branch_calls) == 3

    _poll(client)  # 第 4 拍：不再换基，叫人
    assert len(fake.update_branch_calls) == 3
    note = _cards(client, tid)[0]["note"]
    assert "反复落后" in note
    assert "人工" in note


def test_a_required_check_missing_too_long_goes_to_a_human(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.check_runs_by_sha[head_sha] = []

    _poll(client)  # 第一拍：等 CI，不打扰人
    card = _cards(client, tid)[0]
    assert card["merge_state"]["state"] == "blocked"
    assert card["merge_state"]["who"] == "ci"
    assert "迟迟没有报到" not in (card["note"] or "")

    _set_merge_since(client, cid, "2020-01-01T00:00:00+00:00")
    _poll(client)
    note = _cards(client, tid)[0]["note"]
    assert "迟迟没有报到" in _cards(client, tid)[0]["note"] or "test" in note


def test_poll_reads_with_the_read_mint_not_the_write_one(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "还在跑")

    _poll(client)

    assert set(fake.status_tokens) == {"ghs_app_read"}
    assert set(fake.list_check_runs_tokens) == {"ghs_app_read"}


def test_poll_ignores_settled_and_prless_cards(client, app_world):
    fake = app_world["fake"]
    # A PR-less pending card (unbound-ish shape) …
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid)
    # … and a settled one.
    pid2, tid2, cid2, number, head_sha = _ready_card(client, app_world, number=8)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    assert _accept(client, cid2).status_code == 200

    result = _poll(client)
    assert result["cards_checked"] == 0
    assert result["errors"] == []


def test_a_closed_unmerged_pr_notes_once_and_the_poller_idles(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.close_unmerged(number)

    result = _poll(client)
    assert result["errors"] == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert "关闭" in card["note"] and "没有合并" in card["note"]
    assert fake.merge_calls == []

    note = card["note"]
    _poll(client)  # 60s 轮询：说一次就够
    assert _cards(client, tid)[0]["note"] == note


def test_poll_settles_an_externally_merged_pr(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "lint 挂了")  # 红着也照收
    merged_at = datetime(2026, 8, 9, 22, 3, 59, tzinfo=UTC)
    fake.merge_externally(number, merge_commit_sha="a34b8e12", merged_at=merged_at)

    _poll(client)

    card = _cards(client, tid)[0]
    assert card["status"] == "accepted"
    assert "人工合并" in card["note"]
    assert "2026-08-09T22:03:59" in card["pr_merged_at"]
    assert fake.merge_calls == []
    delivered = _topic(client, tid)
    assert delivered["status"] == "active"
    assert delivered["accepted_at"] is not None


def test_poll_steady_state_costs_one_pr_read_per_tick(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "还在跑")

    _poll(client)
    _poll(client)

    assert fake.status_calls == [number, number]
    assert fake.head_sha_calls == []


# ============================ 绿了自动合 =====================================


def test_arming_needs_the_project_setting_and_the_reviewer(client, app_world):
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)

    r = _arm(client, cid, "alice")
    assert r.status_code == 422, r.text  # 项目没开 auto_merge_allowed

    _protect(client, pid, auto_merge_allowed=True)
    assert _arm(client, cid, "mallory").status_code == 403  # 不是验收人

    r = _arm(client, cid, "alice")
    assert r.status_code == 200, r.text
    card = r.json()["data"]
    assert card["auto_merge"]["armed_by"] == "alice"
    # 布防不是决议：卡留在 pending。
    assert card["status"] == "pending"
    assert card["decided_by"] is None


def test_an_armed_card_merges_when_the_rules_turn_green(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, auto_merge_allowed=True, required_checks=[{"name": "test"}])
    fake.check_state_by_sha[head_sha] = ("failure", "pytest: 1 failed")
    assert _arm(client, cid, "alice").status_code == 200

    _poll(client)
    wait_work_idle()
    assert fake.merge_calls == []  # 红着不合

    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)

    card = _cards(client, tid)[0]
    assert card["status"] == "accepted"
    assert card["decided_by"] == "alice"  # 以布防人的名义
    assert "alice" in card["approvals"]
    assert [m["sha"] for m in fake.merge_calls] == [head_sha]  # 同样带 sha


def test_a_new_commit_disarms_the_auto_merge(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, auto_merge_allowed=True)
    fake.check_state_by_sha[head_sha] = ("pending", "还在跑")
    _poll(client)  # 镜像 head
    assert _arm(client, cid, "alice").status_code == 200

    new_sha = fake.push_new_commit(number)
    fake.check_state_by_sha[new_sha] = ("success", "全绿")
    _poll(client)

    card = _cards(client, tid)[0]
    assert card["auto_merge"]["armed_by"] is None  # 新提交作废布防
    assert card["status"] == "pending"
    assert fake.merge_calls == []


def test_an_armed_merge_still_needs_enough_votes(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, auto_merge_allowed=True, approvals_required=2)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)
    assert _arm(client, cid, "alice").status_code == 200

    _poll(client)
    assert fake.merge_calls == []
    assert "批准人数不足" in _cards(client, tid)[0]["note"]

    _approve(client, cid, "bob")
    _poll(client)
    assert _cards(client, tid)[0]["status"] == "accepted"


# ============================ 人工放行 =======================================


def test_merge_anyway_is_for_humans_only(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

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

    # 不在放行名单里的人也不行（默认名单 = owner + lead）。
    assert _merge_anyway(client, cid, "mallory").status_code == 403

    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"


def test_merge_anyway_merges_and_signs_the_card(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.check_state_by_sha[head_sha] = ("failure", "Backend Test: failure")

    # 先证明规则真的拦着。
    assert _accept(client, cid).status_code == 422
    assert fake.merge_calls == []

    # alice 是项目 owner —— 默认放行名单里的人。
    r = _merge_anyway(client, cid, "alice", reason="CI runner 挂了，跟这次改动无关")
    assert r.status_code == 200, r.text
    card = r.json()["data"]

    assert [m["number"] for m in fake.merge_calls] == [number]
    assert card["status"] == "accepted"
    assert card["decided_by"] == "alice"
    delivered = _topic(client, tid)
    assert delivered["status"] == "active"
    assert delivered["accepted_at"] is not None
    # 署名：谁、理由、以及合并那一刻检查到底是什么状态。
    assert "alice" in card["note"]
    assert "CI runner 挂了" in card["note"]
    assert "明知检查未全绿仍合并" in card["note"]


def test_merge_anyway_admission_follows_the_override_roster(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, override_handles=["carol"])
    fake.check_state_by_sha[head_sha] = ("failure", "红")

    # 显式名单顶掉默认：连 owner 都不在名单里就不能放行。
    assert _merge_anyway(client, cid, "alice").status_code == 403
    r = _merge_anyway(client, cid, "carol", reason="我来背")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"


def test_merge_anyway_on_a_green_pr_is_not_recorded_as_knowingly_red(client, app_world):
    """PR #520 的教训：当时全绿就写全绿，别往历史里写一条没发生过的决定。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全部 5 项检查通过")

    r = _merge_anyway(client, cid, "alice", reason="等不及了")
    assert r.status_code == 200, r.text
    note = r.json()["data"]["note"]
    assert "明知检查未全绿" not in note
    assert "全绿" in note


def test_merge_anyway_when_the_check_state_is_unreadable_says_so(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_error = RuntimeError("GitHub 连不上")
    # 镜像 head 先落卡（check_state 坏了不拦 status 读取）。
    fake.check_state_error = None
    _poll(client)
    fake.check_state_error = RuntimeError("GitHub 连不上")

    r = _merge_anyway(client, cid, "alice", reason="CI 读不到，但改动我看过了")
    assert r.status_code == 200, r.text
    note = r.json()["data"]["note"]
    assert "读不到检查状态" in note
    assert "明知检查未全绿" not in note


def test_merge_anyway_is_refused_once_the_card_is_settled(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    assert _accept(client, cid).status_code == 200

    assert _merge_anyway(client, cid, "alice").status_code == 422
    assert len(fake.merge_calls) == 1  # 没有第二次合并


# ============================ push-fix 与本地分支 ============================


def _real_git_head(project_id: _uuid.UUID, topic_id: _uuid.UUID) -> str:
    from app.domain.workspace import service as ws

    repo_path = ws.ensure_repo(project_id)
    branch = ws.branch_for_tree(topic_id)
    return subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_push_fix_puts_the_local_commit_on_the_pr_on_demand(
    client, app_world, monkeypatch
):
    """轮询器不再自动重推（#718 删掉了那件事）：工作区的新提交上 PR 的唯一通道
    是 push-fix。这里驱动真实的本地 git 读取（`_local_topic_branch_head` 恢复成
    真实实现），只有到 github.com 的网络一跳是假的。"""
    from app.domain.review.services import AcceptService
    from app.domain.workspace import service as ws

    # app_world 默认把本地 head 钉成 None；这条测试要真的读 git。
    monkeypatch.setattr(
        AcceptService,
        "_local_topic_branch_head",
        lambda _self, pid, tid: _real_git_head(pid, tid),
    )
    monkeypatch.setattr(
        AcceptService, "_remote_head_ff_from_local", lambda *_a, **_k: True
    )
    fake = app_world["fake"]
    pushes: list[dict] = []
    holder: dict = {"number": None}

    def real_head_push(project_id, topic_id, *, owner, repo, remote_branch, token):
        head_sha = _real_git_head(project_id, topic_id)
        pushes.append({"remote_branch": remote_branch, "head_sha": head_sha})
        if holder["number"] is not None:
            fake.prs[holder["number"]]["head_sha"] = head_sha
        return {"head_sha": head_sha, "remote_branch": remote_branch}

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", real_head_push)

    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    holder["number"] = number
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

    machine_commits(puid, tuid, {"work.txt": "first pass\n"})
    first_head = _real_git_head(puid, tuid)
    fake.prs[number]["head_sha"] = first_head

    _poll(client)
    card = _cards(client, tid)[0]
    assert card["pr_head_sha"] == first_head
    assert pushes == []  # 轮询绝不自动推

    machine_commits(puid, tuid, {"work.txt": "fixed\n"})
    pushed = client.post(
        f"/topics/{tid}/push-fix", headers=session_auth_headers("alice")
    ).json()["data"]
    assert pushed["pushed"] is True
    second_head = pushes[-1]["head_sha"]
    assert second_head != first_head
    assert _cards(client, tid)[0]["pr_head_sha"] == second_head

    again = client.post(
        f"/topics/{tid}/push-fix", headers=session_auth_headers("alice")
    ).json()["data"]
    assert again["pushed"] is False, "没有新东西可推时,再问一次不算错误"


def test_push_fix_declines_a_doomed_non_fast_forward_and_says_so(
    client, app_world, monkeypatch
):
    from app.domain.review.services import AcceptService
    from app.domain.workspace import service as ws

    monkeypatch.setattr(
        AcceptService,
        "_local_topic_branch_head",
        lambda _s, _p, _t: "moved-but-diverged",
    )
    monkeypatch.setattr(
        AcceptService, "_remote_head_ff_from_local", lambda *_a, **_k: False
    )
    pushes: list[dict] = []

    def spy_push(_pid, _tid, *, owner, repo, remote_branch, token):
        pushes.append({"remote_branch": remote_branch})
        return {"head_sha": "must-not-happen", "remote_branch": remote_branch}

    monkeypatch.setattr(ws, "push_topic_branch_for_github_pr", spy_push)

    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    pushed = client.post(
        f"/topics/{tid}/push-fix", headers=session_auth_headers("alice")
    ).json()["data"]

    assert pushed["pushed"] is False
    assert pushes == []  # 注定失败的推送一次都没发生
    card = _cards(client, tid)[0]
    assert card["note"].startswith("本地分支与 PR 分支已分叉")
    assert card["note_level"] == "error"


def test_remote_head_ff_from_local_reads_real_git_ancestry(client):
    """fast-forward 预检读的是真实 git ancestry（946bf5de 的回归面）。"""
    from types import SimpleNamespace

    from app.domain.review.services import AcceptService

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

    machine_commits(puid, tuid, {"a.txt": "1\n"})
    head1 = _real_git_head(puid, tuid)
    machine_commits(puid, tuid, {"a.txt": "2\n"})
    head2 = _real_git_head(puid, tuid)
    assert head1 != head2

    svc = AcceptService(SimpleNamespace())
    assert svc._remote_head_ff_from_local(puid, head1, head2) is True
    assert svc._remote_head_ff_from_local(puid, head2, head1) is False
    assert svc._remote_head_ff_from_local(puid, "0" * 40, head2) is False


def test_an_unreadable_verdict_stops_the_accept_instead_of_guessing(client, app_world):
    """读不到检查/合并态不是绿：点击可见地停下、可重试——绝不落本地合并，
    也绝不当作可合。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_error = RuntimeError("check-runs read failed (HTTP 500)")

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "采纳未完成" in r.json()["message"]
    assert fake.merge_calls == []
    assert app_world["local_merges"] == []
    assert _cards(client, tid)[0]["status"] == "pending"


def test_a_failed_post_merge_sync_is_annotated_not_fatal(
    client, app_world, monkeypatch
):
    """合完同步本地 base 失败不能吞掉采纳本身——合并已是事实，卡如实带上
    「本地同步待补」。"""
    from app.domain.workspace import service as ws

    def _sync_fails(_pid, token=None):
        raise RuntimeError("fetch upstream failed")

    monkeypatch.setattr(ws, "sync_upstream", _sync_fails)
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "本地同步待补" in card["note"]
