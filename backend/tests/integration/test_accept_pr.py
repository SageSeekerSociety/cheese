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
import threading
import time
import uuid
import uuid as _uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.announce import announce
from app.domain.delivery.agent import (
    record_task_instruction,
)
from app.domain.delivery.ledger import settle_card
from app.domain.review import github_pr
from app.domain.review.github_pr import OpenedPR
from app.domain.review.github_pr_status import PullRequestStatus
from app.domain.review.pr_publish import dispatch as _REAL_DISPATCH
from tests.conftest import wait_work_idle
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    room_text,
    session_auth_headers,
)
from tests.landing import written_up
from tests.machine_work import machine_commits
from tests.support import git_store
from tests.support.review_ports import task_teammate

REPO = "acme/widgets"


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"}, headers=session_auth_headers("alice"))
    assert r.status_code == 200
    pid = r.json()["data"]["id"]
    # alice owns the project she files cards in, and bob is the second voter the
    # dismiss-stale / armed-merge cases need. Approving and deciding a card are
    # room decisions: since 2026-09-26 they require membership (`_card_actor`),
    # so the second voter is a participant like anyone else.
    join_project_team(client, pid, "bob")
    return pid


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card_response(client, topic_id: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "focus": "最懂",
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


def _rendered_revision(client, card_id: str) -> str | None:
    """卡面上交付物的那一版（payload 的 `deliverable.revision`），读法和浏览器
    一样：从卡所在房间的卡列表里取。交合并的卡没有这一项，是 None。"""
    from app.domain.review.repositories import AcceptCardRepository

    async def _room() -> str:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None
            return str(card.topic_id)

    listed = client.get(
        f"/topics/{asyncio.run(_room())}/accept-card",
        headers=session_auth_headers("alice"),
    )
    assert listed.status_code == 200, listed.text
    (card,) = [c for c in listed.json()["data"]["data"] if c["id"] == card_id]
    return (card.get("deliverable") or {}).get("revision")


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


def _chat_service():
    """生产上轮询器拿到的就是这一个 ChatService（`deps.get_chat_service` 的单例）。"""
    from app.api.deps import get_chat_service
    from app.main import app

    return app.dependency_overrides[get_chat_service]()


def _poll(client) -> dict:
    """跑一轮合并态轮询。

    在 TestClient 自己的 portal 上跑，而不是新起一个事件循环：轮询会 `submit`
    真正的轮次，它们得落在 work runner 所在的那个循环上——生产上这一轮也正是
    从那里跑的（`app/domain/review/periodic.py` 的 "pr poll"）。
    """
    from app.domain.review import pr_poll

    return client.portal.call(
        lambda: pr_poll.poll_open_prs(
            _chat_service(),
            announce,
            settle_card=settle_card,
            record_instruction=record_task_instruction,
            task_teammate=task_teammate,
        )
    )


def _backfill_merge_actors(client) -> dict:
    """跑一次合并人补录（项目总览还看得到的那 14 天，上线时那一次）。"""
    from app.domain.review import pr_poll

    return client.portal.call(
        lambda: pr_poll.backfill_missing_merge_actors(_chat_service())
    )


def _stored_merged_by(client, card_id: str) -> dict | None:
    from app.domain.review.repositories import AcceptCardRepository

    async def _do():
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(_uuid.UUID(card_id))
            assert card is not None
            return card.merged_by

    return asyncio.run(_do())


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


def _task_settled(client, topic_id: str, needle: str) -> str:
    """一张卡的结局说在递它的那个任务里，频道主线上那件任务只占一行。"""
    return _room_settled(client, str(delivery_task_id(client, topic_id)), needle)


def _room_event_count(client, topic_id: str, event_type: str) -> int:
    """这个房间落了几块这种事件。同一件事每拍重说一遍，这个数就涨。

    查库里那些块，不走 `/topics/{id}/blocks`：平台提示送到人那里，不占房间可见的
    那条时间线。
    """
    from sqlalchemy import select

    from app.domain.block.models import Block
    from app.domain.conversation.services import of_room

    async def go() -> int:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block).where(of_room(Block.conversation_id, uuid.UUID(topic_id)))
            )
            return sum(
                1 for r in rows if (r.meta or {}).get("event_type") == event_type
            )

    return asyncio.run(go())


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
        # How long GitHub takes to say where a PR stands.
        self.status_delay_s: float = 0.0
        # (entered, release): a status read sets `entered` and then waits for
        # `release`, so a test can act while a reading is in flight.
        self.status_gate: tuple[threading.Event, threading.Event] | None = None
        self.opened: list[dict] = []
        self.merge_calls: list[dict] = []
        self.status_calls: list[int] = []
        self.task_metadata_batches: list[tuple[int, ...]] = []
        self.status_tokens: list[str] = []
        self.head_sha_calls: list[int] = []
        self.check_state_tokens: list[str] = []
        self.list_check_runs_tokens: list[str] = []
        self.reviews_by_number: dict[int, list] = {}
        self.review_signal_calls: list[tuple[int, bool]] = []

    # ---- test helpers ------------------------------------------------------

    def seed_pr(
        self, number: int, *, head: str, base: str = "main", title: str = ""
    ) -> str:
        """Register a PR (the App lane opens PRs through a different client, so
        the poller side has to be told it exists). Returns its head sha.

        `title` 是**平台按卡片 subject 写上去的那一行**（`pr_publish.sync_pr_text`）：
        两条合并路径都比对它和卡片，不一致就停下（`pr_text.pr_title_matches_subject`），
        所以这里不是可选的装饰 —— 一个不带标题的 PR 会被读成「标题读不出来」而合不
        了（`_give_card_a_pr` 因此默认取卡片自己的 subject）。"""
        head_sha = f"sha-{head}-1"
        self.prs[number] = {
            "head": head,
            "base": base,
            "title": title,
            "head_sha": head_sha,
            "state": "open",
            "merged": False,
            "merge_commit_sha": None,
            "merged_at": None,
            "merged_by": None,
        }
        return head_sha

    def merge_externally(
        self,
        number: int,
        *,
        merge_commit_sha: str | None = "human-merge-sha",
        merged_at: datetime | None = None,
        merged_by: dict | None = None,
    ) -> None:
        self.prs[number].update(
            state="closed",
            merged=True,
            merge_commit_sha=merge_commit_sha,
            merged_at=merged_at,
            merged_by=merged_by,
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
        head_sha = self.seed_pr(number, head=head, base=base, title=title)
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
    ) -> PullRequestStatus:
        if self.status_gate is not None:
            entered, release = self.status_gate
            entered.set()
            await asyncio.to_thread(release.wait, 30)
        if self.status_delay_s:
            await asyncio.sleep(self.status_delay_s)
        if self.status_error is not None:
            raise self.status_error
        pr = self.prs[number]
        self.status_calls.append(number)
        self.status_tokens.append(token)
        return PullRequestStatus(
            head_sha=pr["head_sha"],
            head_ref=pr["head"],
            base_ref=pr["base"],
            state=pr["state"],
            merged=pr["merged"],
            merge_commit_sha=pr["merge_commit_sha"],
            merged_at=pr["merged_at"],
            mergeable=self.mergeable_by_number.get(number),
            mergeable_state=self._derived_mergeable_state(number),
            draft=self.draft_by_number.get(number, False),
            title=pr.get("title") or "",
            review_comment_count=sum(
                1 for s in self.reviews_by_number.get(number, []) if s.kind == "comment"
            ),
            merged_by_github_id=(pr.get("merged_by") or {}).get("github_id"),
            merged_by_login=(pr.get("merged_by") or {}).get("login"),
        )

    async def task_pr_metadata(self, *, owner, repo, numbers, token):
        # deferred-import: pre-batch revisions use this fake's single-PR operation.
        from app.domain.review.github_pr.task_pr_metadata import (
            TaskPrMetadata,
            TaskPrMetadataFailure,
        )

        if self.status_delay_s:
            await asyncio.sleep(self.status_delay_s)
        if self.status_error is not None:
            raise self.status_error
        self.task_metadata_batches.append(tuple(numbers))
        return {
            number: (
                TaskPrMetadata(number, pr["head_sha"], pr["state"], pr["merged"])
                if (pr := self.prs.get(number)) is not None
                else TaskPrMetadataFailure("missing_pr")
            )
            for number in numbers
        }

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

    async def branch_comparisons(self, *, owner, repo, pairs, token):
        raise AssertionError("app_world must supply its Git-backed branch comparisons")

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
    installation_id = 1
    #: What GitHub's `/rate_limit` says is left of the installation's hour.
    quota_left = 5000

    async def core_quota(self) -> tuple[int, int]:
        return type(self).quota_left, 5000

    async def write_token(self) -> tuple[str, str]:
        type(self).minted_write += 1
        return "ghs_app_write", "2099-01-01T00:00:00+00:00"

    async def installation_token(self) -> tuple[str, str]:
        type(self).minted_read += 1
        return "ghs_app_read", "2099-01-01T00:00:00+00:00"


@pytest.fixture
def app_world(client, monkeypatch):
    """一个接了平台 GitHub App、有 GitHub upstream 的项目 —— `ForgeKind.github_app`。

    The fake records proposal edits and remote merges. Repository protection is
    disabled so the project approval policy is exercised by these tests.
    """
    from app.domain.project import forge as project_forge
    from app.domain.project import github_app
    from app.domain.project.models import ProjectForge
    from app.domain.repository import forge_files
    from app.domain.review import forge_clients, pr_publish
    from app.domain.review import services as review_services

    fake = FakeGitHubPrClient()
    recorded: dict = {
        "fake": fake,
        "opened": [],
        "patched": [],
        "readied": [],
        "prs_by_head": {},
    }
    _FakeTokens.minted_write = 0
    _FakeTokens.minted_read = 0
    _FakeTokens.quota_left = 5000

    class _AppPrOpener:
        """`pr_publish` 用来开 PR 的那只 client（App token，大写 PR 的那个）。
        开出来的 PR 同时登记进 `fake`，因为之后点击/轮询读的是另一只 client。

        一条 head 上只有一个开着的 PR：同一条分支再开一次，拿回的是同一个
        （真 GitHub 是 422「already exists」+ 查回来，`open_pr` 内部做的）。
        `update_pr` / `mark_ready_for_review` 在这里是真的会改状态的，因为
        「认领来的 draft PR 会被改写文案并翻成 ready」正是递卡这一步的行为。"""

        def __init__(self, owner: str, repo: str, tokens, **_):
            self.owner, self.repo = owner, repo
            self.tokens = tokens

        async def open_pr(
            self,
            *,
            head: str,
            base: str,
            title: str,
            body: str,
            draft: bool = False,
            as_user_token: str | None = None,
        ) -> OpenedPR:
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
                return OpenedPR(adopted)
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
            fake.seed_pr(number, head=head, base=base, title=title)
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
            return OpenedPR(pr)

        async def update_pr(
            self, number: int, *, title=None, body=None, base=None
        ) -> dict:
            fields = {
                key: value
                for key, value in {"title": title, "body": body, "base": base}.items()
                if value is not None
            }
            recorded["patched"].append({"number": number, **fields})
            # 改写真的落到另一只 client 读得到的地方：`base` 决定「目标分支是
            # 哪条」，`title` 是平台按卡片 subject 重写的那一行（`redescribe` 走
            # 这里），两条合并路径都读它。
            if number in fake.prs:
                for key in ("base", "title"):
                    if key in fields:
                        fake.prs[number][key] = fields[key]
            for pr in recorded["prs_by_head"].values():
                if pr["number"] == number:
                    pr.update(fields)
                    return pr
            return {"number": number, **fields}

        async def mark_ready_for_review(self, node_id: str) -> None:
            recorded["readied"].append(node_id)
            for pr in recorded["prs_by_head"].values():
                if pr.get("node_id") == node_id:
                    pr["draft"] = False
                    fake.draft_by_number[pr["number"]] = False

        async def pr_view(self, number: int) -> dict:
            for pr in recorded["prs_by_head"].values():
                if pr["number"] == number:
                    return pr
            # 生产上这两只 client 读的是同一个仓库：递卡时就已经开好的 PR
            # （`_give_card_a_pr` 直接 `fake.seed_pr` 登记的，轮询/点击那一路看的）
            # 在这里也读得到。少了这一步，`redescribe` 按卡改 PR 标题时会在一只
            # client 找不到另一只登记过的 PR。
            if number in fake.prs:
                return {
                    "number": number,
                    "html_url": f"https://github.com/{REPO}/pull/{number}",
                    "title": fake.prs[number].get("title") or "",
                }
            raise AssertionError(f"no such PR: {number}")

        async def pr_status(self, number: int):
            return await fake.pull_request_status(
                owner="owner", repo="repo", number=number, token="ghs_app_read"
            )

        async def task_pr_metadata(self, numbers):
            return await fake.task_pr_metadata(
                owner="owner", repo="repo", numbers=numbers, token="ghs_app_write"
            )

    async def _tokens_for_project(_project_id, _session):
        return _FakeTokens()

    monkeypatch.setattr(
        github_app, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(
        project_forge, "github_app_tokens_for_project", _tokens_for_project
    )
    monkeypatch.setattr(forge_clients, "GitHubPRClient", _AppPrOpener)

    async def provision(project_id, session, **kwargs):
        binding = ProjectForge(
            project_id=project_id,
            kind="github_app",
            repo=REPO,
            url=f"https://github.com/{REPO}.git",
            api_url="https://api.github.com",
            default_branch="main",
        )
        session.add(binding)
        await session.flush()
        return binding

    async def default_branch(*args):
        return "main"

    async def branch_head(project_id, session, branch, **_):
        repo = git_store.path(project_id)
        if not repo.exists():
            return None
        result = git_store.run(["git", "rev-parse", "--verify", branch], repo, 20, None)
        if result.returncode != 0:
            return None
        head = result.stdout.strip()
        ahead = int(git_store.git(repo, "rev-list", "--count", f"main..{head}"))
        fake.compare_status_by_pair.setdefault(
            ("main", head), "ahead" if ahead else "identical"
        )
        return head

    async def branch_comparisons(*, owner, repo, pairs, token):
        from sqlalchemy import select

        from app.domain.review.github_pr.protocol import BranchComparison
        from app.domain.room_task.models import Task

        async with client.test_request_factory() as session:
            rows = (
                await session.execute(
                    select(Task.branch_name, Task.project_id).where(
                        Task.branch_name.in_([branch for branch, _base in pairs])
                    )
                )
            ).all()
            projects = {branch: project_id for branch, project_id in rows}
        result = {}
        for branch, base in pairs:
            project_id = projects.get(branch)
            head = (
                await pr_publish.branch_head(project_id, session, branch)
                if project_id
                else None
            )
            if head is None:
                result[(branch, base)] = None
                continue
            path = git_store.path(project_id)
            ahead = int(git_store.git(path, "rev-list", "--count", f"{base}..{head}"))
            fake.compare_status_by_pair.setdefault(
                (base, head), "ahead" if ahead else "identical"
            )
            status = await fake.compare_status(
                owner=owner, repo=repo, base=base, head=head, token=token
            )
            result[(branch, base)] = BranchComparison(
                head, int(status in ("ahead", "diverged"))
            )
        return result

    monkeypatch.setattr(fake, "branch_comparisons", branch_comparisons)

    async def repository_data(project_id, session, path="", **kwargs):
        from urllib.parse import unquote

        assert path.startswith("/compare/"), path
        base, head = unquote(path.removeprefix("/compare/")).split("...")
        repo = git_store.path(project_id)
        return {
            "total_commits": int(
                git_store.git(repo, "rev-list", "--count", f"{base}..{head}")
            ),
            "files": [
                {"filename": name}
                for name in git_store.git(
                    repo, "diff", "--name-only", f"{base}...{head}"
                ).splitlines()
            ],
        }

    monkeypatch.setattr(project_forge, "provision_repository", provision)

    # A project started from a task gets its repository prepared and then
    # bound (`ProjectService.for_participation`), not provisioned: it joins
    # this world the same way.
    async def bind_repository(session, prepared):
        return await provision(prepared.project_id, session)

    monkeypatch.setattr(project_forge, "bind_repository", bind_repository)
    monkeypatch.setattr(project_forge, "default_branch", default_branch)
    monkeypatch.setattr(project_forge, "branch_head", branch_head)
    monkeypatch.setattr(pr_publish, "branch_head", branch_head)
    monkeypatch.setattr(forge_files, "branch_head", branch_head)
    monkeypatch.setattr(forge_files, "repository_data", repository_data)
    # `cheese ready` / 更正描述走 `AcceptService._app_pr_client`，它在调用时
    # 从 github_pr 模块取这个名字 —— 同一只假 client，同一批 PR。
    monkeypatch.setattr(github_pr, "GitHubPRClient", _AppPrOpener)
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
    monkeypatch.setattr(
        review_services, "async_session_factory", client.test_request_factory
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
    branch = _disk_branch(client, topic_id)

    from app.domain.review.repositories import AcceptCardRepository

    async def _do() -> str:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None
            # 平台递卡时按卡片的 subject 写 PR 标题（`pr_publish.sync_pr_text`），
            # 所以这张假 PR 的标题就是卡片自己的那一行 —— 少了它，两条合并路径
            # 都会把这张卡读成「标题和卡片不一致」而拒绝合并。
            head_sha = app_world["fake"].seed_pr(
                number, head=branch, title=card.change_subject or ""
            )
            card.pr_number = number
            card.pr_url = f"https://github.com/{REPO}/pull/{number}"
            if mirrored:
                card.pr_head_sha = head_sha
            await s.commit()
            return head_sha

    return asyncio.run(_do())


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


@pytest.mark.parametrize("removed", [False, True])
@pytest.mark.parametrize("action", ["accept", "override", "auto"])
def test_queued_accept_stays_pending_until_github_merges(
    client, app_world, monkeypatch, removed, action
):
    fake = app_world["fake"]
    pid, tid, cid, number, head = _ready_card(client, app_world)
    fake.check_state_by_sha[head] = ("success", "green")
    enqueues = []

    async def enqueue(**kwargs):
        enqueues.append(kwargs)
        return github_pr.MergeResult(queued=True)

    async def in_queue(**kwargs):
        return not removed

    monkeypatch.setattr(fake, "merge_pull_request", enqueue)
    monkeypatch.setattr(fake, "merge_queue_entry", in_queue, raising=False)
    if action == "auto":
        _protect(client, pid, auto_merge_allowed=True)
        response = _arm(client, cid, "alice")
        _poll(client)
    elif action == "override":
        response = _merge_anyway(client, cid, "alice", reason="test")
    else:
        response = _accept(client, cid)
    assert response.status_code == 200, response.text
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_merged_at"] is None
    _poll(client)
    _poll(client)
    assert len(enqueues) == 1
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    if removed:
        assert "离开合并队列" in card["note"]
    else:
        assert "等待队列检查" in card["note"]
        fake.merge_externally(number)
        _poll(client)
        card = _cards(client, tid)[0]
        assert card["status"] == "accepted"
        assert card["decided_by"] == "alice"


def _queue_mirror(client, card_id: str) -> dict:
    """卡面上那份镜像，直接从库里读。

    读卡这条路（`_cards`）自己会把过期的快照补上（`refresh_stale_pr_snapshots`），
    要测轮询写下的那一份就不能从它那里读。"""
    from app.domain.review.repositories import AcceptCardRepository

    async def _do() -> dict:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None and isinstance(card.merge_state, dict)
            return dict(card.merge_state)

    return asyncio.run(_do())


def _age_queue_mirror(client, card_id: str, *, seconds: int) -> str:
    """把镜像的 `checked_at` 推回 `seconds` 秒之前，理由和状态都不动 ——
    摆成「上一次看这张卡是很久以前」，返回那个时刻。"""
    from app.domain.review.repositories import AcceptCardRepository

    stale_at = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()

    async def _do() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None and isinstance(card.merge_state, dict)
            card.merge_state = {**card.merge_state, "checked_at": stale_at}
            await s.commit()

    asyncio.run(_do())
    return stale_at


def _watch_window() -> int:
    """「还在等 CI」的持有窗口，秒 —— `queries.CHECK_WATCH_TICKS` 个轮询间隔
    （默认 3 × 60）。拨时钟的用例照它算，别复写「900」「300」这类数字：
    `accept_pr_poll_interval_s` 一动它们就悄悄错位，而错位的那一侧是断言不
    再钉在边界上（2026-10-16 300→60 时这里红过一次）。"""
    from app.core.config import settings
    from app.domain.review.queries import CHECK_WATCH_TICKS

    return CHECK_WATCH_TICKS * settings.accept_pr_poll_interval_s


def _waiting_on_checks(client, topic_id: str) -> bool:
    """环境释放判据读的就是它（`machine.lifecycle`）。"""
    from app.domain.review.queries import waiting_on_checks

    task_id = _uuid.UUID(str(delivery_task_id(client, topic_id)))

    async def _do() -> bool:
        async with client.test_factory() as s:
            return await waiting_on_checks(s, task_id)

    return asyncio.run(_do())


def test_a_queued_card_keeps_refreshing_its_mirror(client, app_world, monkeypatch):
    """卡在合并队列里时，镜像照样一轮刷一次 —— 「还在等 CI」的持有不该在队列
    里比普通路径短，也不该被读卡那条路抹掉。

    队列里 PR 的 `mergeStateStatus` 被 GitHub 钉在 BLOCKED，判定重算没有意义，
    所以进队以后不再重算它，只由轮询每跳把 `checked_at` 推到此刻（`state`、
    `reasons` 一条不动）。`checked_at` 是「还在等 CI」这句话的有效期：冻在入队
    那一刻，过 `CHECK_WATCH_TICKS` 个轮询间隔（默认 3 分钟）就没人再当这张卡在
    等 CI，环境释放判据把机器收掉 —— 而轮询其实一直在看，回头还要叫醒这条线重建
    环境、重读文档。队列里这段和普通路径是同一件事，就该挂同一根钟（1 小时）。

    只续期还不够：读卡那条路（`_cards` → `refresh_stale_pr_snapshots`）对过期的
    快照也会重算一遍，而它重算出来的那份不带 `ci_running`（队列自己那套检查它
    看不见），浏览器每 15 秒来读一次就把话抹了。队列里的卡不归它管。"""
    fake = app_world["fake"]
    _pid, tid, cid, _number, head = _ready_card(client, app_world)
    fake.check_state_by_sha[head] = ("success", "green")

    async def enqueue(**kwargs):
        return github_pr.MergeResult(queued=True)

    async def in_queue(**kwargs):
        return True

    monkeypatch.setattr(fake, "merge_pull_request", enqueue)
    monkeypatch.setattr(fake, "merge_queue_entry", in_queue, raising=False)

    assert _accept(client, cid).status_code == 200
    enqueued = _queue_mirror(client, cid)  # 入队那一刻写下的那份

    _poll(client)  # 队列里的一跳
    first = _queue_mirror(client, cid)
    # 和 `enqueued` 比，不和上一跳比：轮询每跳写的都是同一份字面量，两个轮询帧
    # 之间按构造必然相等，那样的断言永远不会红。要问的是「这一跳有没有换个说法」
    # —— 换了（比如重算成 GitHub 钉住的那份）这里就红。
    assert first["reasons"] == enqueued["reasons"]  # 判定一条没改
    assert first["checked_at"] > enqueued["checked_at"]  # 只有 checked_at 走

    # 摆成「上一次看这张卡是三个轮询间隔以前」：不再刷的话，判据就不认它了。
    stale_at = _age_queue_mirror(client, cid, seconds=_watch_window() + 1)
    assert not _waiting_on_checks(client, tid)

    _poll(client)  # 队列里的下一跳：把有效期续上

    again = _queue_mirror(client, cid)
    # 同样和 `enqueued` 比：判定仍是入队那一刻那份（state 和 reasons 一起看，
    # 两处写入分别在 `merge_queue` 和这里）。
    assert (again["state"], again["reasons"]) == (
        enqueued["state"],
        enqueued["reasons"],
    )
    assert again["checked_at"] > stale_at
    assert _waiting_on_checks(client, tid)

    # 这时浏览器来读一次卡。读卡这条路对「比地板（`accept_pr_snapshot_floor_s`）
    # 还旧」的快照会自己重算一遍，所以把 `checked_at` 拨到地板之外、持有窗口
    # 之内的一段——两个数都从设置里算，下面那条 assert 钉着这段确实存在：
    # 地板追平窗口时这条用例就没法摆出「读卡会重算、判据却还认」这个状态了。
    from app.core.config import settings as _settings

    floor = _settings.accept_pr_snapshot_floor_s
    window = _watch_window()
    assert floor < window, (
        f"地板的 {floor}s 不小于持有窗口的 {window}s，这条用例摆不出"
        "「读卡重算、但判据仍认它是等 CI」的那段区间"
    )
    aged_at = _age_queue_mirror(client, cid, seconds=(floor + window) // 2)
    assert _waiting_on_checks(client, tid)  # 还在窗口里，判据照样认它

    _cards(client, tid)

    # 队列里的这张不归读卡这条路管：它一重算就会把 `ci_running` 换成它自己看
    # 得见的那份（没有队列检查），持有当场断掉。
    assert _queue_mirror(client, cid)["checked_at"] == aged_at
    assert _waiting_on_checks(client, tid)


@pytest.mark.parametrize("action", ["reject", "void"])
def test_cancelling_queued_card_dequeues_first(client, app_world, monkeypatch, action):
    fake = app_world["fake"]
    _pid, tid, cid, _number, head = _ready_card(client, app_world)
    fake.check_state_by_sha[head] = ("success", "green")
    calls = []

    async def enqueue(**kwargs):
        return github_pr.MergeResult(queued=True)

    async def dequeue(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(fake, "merge_pull_request", enqueue)
    monkeypatch.setattr(fake, "dequeue_pull_request", dequeue, raising=False)
    assert _accept(client, cid).status_code == 200
    response = client.post(
        f"/accept-cards/{cid}/{action}",
        json={"decided_by": "alice", "note": "cancel"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert _cards(client, tid)[0]["status"] in ("rejected", "revoked")


@pytest.mark.parametrize("parent_closed", [False, True])
@pytest.mark.parametrize("action", ["accept", "override", "auto"])
def test_dependent_task_cannot_be_accepted_before_its_parent_is_delivered(
    client, app_world, parent_closed, action
):
    from app.domain.room_task.models import Task, TaskStatus

    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    child_id = delivery_task_id(client, tid)

    async def dependency():
        async with client.test_factory() as session:
            parent = Task(
                project_id=_uuid.UUID(pid),
                room_id=_uuid.UUID(tid),
                title="Required change",
                branch_name="task/parent",
                base_branch="main",
                status=TaskStatus.closed if parent_closed else TaskStatus.open,
            )
            session.add(parent)
            await session.flush()
            child = await session.get(Task, child_id)
            child.base_task_id = parent.id
            child.base_branch = parent.branch_name
            await session.commit()

    asyncio.run(dependency())
    fake.prs[number]["base"] = "task/parent"
    fake.check_state_by_sha[head_sha] = ("success", "全部通过")
    fake.merge_sha_by_number[number] = "child-merge"
    _protect(client, pid, auto_merge_allowed=True)
    assert _approve(client, cid, "alice").status_code == 200
    assert _arm(client, cid, "alice").status_code == 200
    if action == "accept":
        response = _accept(client, cid)
        assert response.status_code == 422, response.text
    elif action == "override":
        response = _merge_anyway(client, cid, "alice")
        assert response.status_code == 422, response.text
    _poll(client)
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["merge_state"]["state"] == "blocked"

    # A native CLI retarget to another unfinished branch does not resolve it.
    fake.prs[number]["base"] = "task/another"
    _poll(client)
    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["merge_state"]["state"] == "blocked"

    # The agent can resubmit against the trunk; reviews of the old diff expire
    # even if the head commit did not change during that retarget.
    fake.prs[number]["base"] = "main"
    _poll(client)
    card = _cards(client, tid)[0]
    assert card["approvals"] == []
    assert card["auto_merge"]["armed_by"] is None
    assert card["pr_head_sha"] is None
    _poll(client)
    assert fake.merge_calls == []
    assert _accept(client, cid).status_code == 200
    assert len(fake.merge_calls) == 1


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
    assert (
        client.get(f"/topics/{delivery_task_id(client, tid)}/task").json()["data"][
            "accepted_at"
        ]
        is not None
    )
    assert delivered["accepted_at"] is None


@pytest.mark.parametrize("merged_remotely", [False, True])
def test_merge_connection_failure_keeps_card_retryable_and_reconciles_remote_result(
    client, app_world, monkeypatch, merged_remotely
):
    fake = app_world["fake"]
    _, tid, cid, number, head = _ready_card(client, app_world)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    merge = fake.merge_pull_request

    async def disconnect(**kwargs):
        if merged_remotely:
            await merge(**kwargs)
        raise ConnectionError("connection lost after request")

    monkeypatch.setattr(fake, "merge_pull_request", disconnect)
    response = _accept(client, cid)
    assert response.status_code == 422, response.text
    assert "合并结果暂时无法确认" in response.json()["message"]
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["approvals"] == []
    assert _topic(client, tid)["status"] == "active"
    assert "采纳未完成" in _task_settled(client, tid, "采纳未完成")
    monkeypatch.setattr(fake, "merge_pull_request", merge)
    retried = _accept(client, cid)
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["status"] == "accepted"
    assert len(fake.merge_calls) == 1
    assert _topic(client, tid)["status"] == "active"


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


def test_github_card_without_a_pr_does_not_claim_checks_are_clean(client, app_world):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid)
    card = _cards(client, tid)[0]
    assert card["pr_number"] is None
    assert card["merge_state"]["state"] == "unknown"
    assert card["forge"]["reports_checks"] is True


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

    assert _arm(client, cid, "alice").status_code == 200
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] == "alice"


def test_a_pr_opened_at_accept_time_is_kept_but_not_merged_this_click(
    client, app_world
):
    """卡还没有 PR、采纳时才现开的那条路（fire-and-forget 失败或还在飞），走的是
    同一条约束。

    「人看的是同一条分支」不等于「同一个 commit」：浏览器从来没有声明过它渲染的
    diff 是哪个 sha，而 AI 边干边推是常态，所以现开的 PR 的 head 照样可能是没人
    看过的那个。开 PR 本身留着（有价值的副作用，下次采纳就有 head 可比），这一次
    不合。"""
    fake = app_world["fake"]
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    assert _cards(client, tid)[0]["pr_number"] is None

    r = _accept(client, cid)

    assert r.status_code == 422, r.text
    assert fake.merge_calls == []  # GitHub 上一次都没合
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


def _github_enforces(monkeypatch) -> None:
    """这个项目的仓库自己开着分支保护/合并队列：平台不再自己判，直接调 GitHub
    的合并 API（`_pr_verdict` 返回的 `enforces`）。真实实现要打 HTTP，`app_world`
    把它钉成 False（「平台补位」那条路），走强制那条路的用例自己钉成 True。"""
    from app.domain.review import services as review_services

    async def _enforces(_repo, _token):
        return True

    monkeypatch.setattr(review_services, "_github_enforces", _enforces)


def _room_event_settled(
    client, topic_id: str, event_type: str, *, times: int = 1, tries: int = 40
) -> int:
    """房间事件是 fire-and-forget（spawn 出去的），给事件循环几拍落块的时间。"""
    count = _room_event_count(client, topic_id, event_type)
    for _ in range(tries):
        if count >= times:
            break
        time.sleep(0.05)
        count = _room_event_count(client, topic_id, event_type)
    return count


def test_a_conflicting_pr_stops_the_accept_in_a_github_enforced_project(
    client, app_world, monkeypatch
):
    """GitHub 自己开着保护的项目：冲突的 PR 在点击那一刻停下，说清是冲突。

    这道闸过去只在平台补位的项目上（`if not enforces` 那一条），强制项目会带着
    `dirty` 去调合并 API；合并队列不收一个合不进去的 PR，入队那一步抛出的
    `GitHubPrError` 落进调用点笼统的 `except Exception`，成了「PR 的合并结果暂时
    无法确认，请重试以核对仓库状态」——把「先解冲突」读成了「重试一次」，而重试
    不会让冲突消失（2026-10-16 采纳 PR #3927 时的样子）。

    钉 `enforces=True` 是为了让这真的走强制那条路：不钉的话，`if not enforces`
    那道守卫会替这道闸挡住合并调用，测的就不是强制项目了。"""
    fake = app_world["fake"]
    _github_enforces(monkeypatch)
    _, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.mergeable_by_number[number] = False  # GitHub: CONFLICTING / DIRTY

    attempts: list[dict] = []

    async def refused_by_the_queue(**kw):
        # 生产上这是 `_enqueue_if_required` 撞上 GitHub 的拒收：合并队列不要一个
        # 合不进去的 PR，`_queue_request` 因此抛 GitHubPrError。
        attempts.append(kw)
        raise github_pr.GitHubPrError("GitHub did not confirm queue entry")

    monkeypatch.setattr(fake, "merge_pull_request", refused_by_the_queue)

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    message = r.json()["message"]
    assert "冲突" in message
    assert "无法确认" not in message
    assert attempts == []  # 压根没去撞那道墙
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert "冲突" in card["note"]
    assert card["note_level"] == "error"
    assert _room_event_settled(client, tid, "accept_conflict") == 1
    assert _topic(client, tid)["accepted_at"] is None


def test_a_conflicting_pr_stops_the_accept_when_the_platform_enforces(
    client, app_world
):
    """平台补位的项目：冲突同样停下，说清是冲突、卡上写明、房间有事件。

    这条路上过去只有一句 `acceptBlocked`（「现在不能采纳（合并态：dirty）：与
    目标分支冲突」）：一个状态词，卡上没有 note，房间也没有事件——芝士不知道有
    活要干，人也不知道这件事不会自己好。"""
    fake = app_world["fake"]
    _, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.mergeable_by_number[number] = False

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    message = r.json()["message"]
    assert "冲突" in message
    assert "现在不能采纳" not in message  # 不是那句「合并态：dirty」
    assert "无法确认" not in message
    assert fake.merge_calls == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert "冲突" in card["note"]
    assert card["note_level"] == "error"
    assert _room_event_settled(client, tid, "accept_conflict") == 1


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


def test_a_delivery_claim_on_a_branchless_tree_stops_the_accept(
    client, app_world, monkeypatch
):
    """回归（2026-09-07 卡 40be3e1a）：绑定项目 + 树分支缺失 + 卡带交付主张。
    改动被推到了别的分支，树的分支从未存在——开不出 PR，本地合并是 no-op，
    旧路径把卡标成 accepted，而改动没有合进任何地方。现在必须停下、原因落卡。"""
    from app.domain.review import pr_publish

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)  # 递卡时分支还在（app_world 默认 True）

    # 采纳时分支没了/从未推上树：改动在别的分支上。
    async def missing_head(*args, **kwargs):
        return None

    monkeypatch.setattr(pr_publish, "branch_head", missing_head)

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "没有任何提交" in r.json()["message"]

    card = _cards(client, tid)[0]
    assert card["status"] == "pending"  # 不落 accepted
    assert "没有任何提交" in card["note"]  # 原因写在卡上
    assert card["note_level"] == "error"
    assert app_world["opened"] == []  # 没开 PR
    assert app_world["fake"].merge_calls == []  # 没合 PR
    assert _topic(client, tid)["accepted_at"] is None


def test_a_legacy_discussion_card_cannot_accept_without_a_remote_branch(
    client, app_world, monkeypatch
):
    """Legacy cards cannot claim delivery through a local no-op merge."""
    from app.domain.review import pr_publish

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _strip_delivery_claim(client, cid)

    async def missing_head(*args, **kwargs):
        return None

    monkeypatch.setattr(pr_publish, "branch_head", missing_head)

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert _cards(client, tid)[0]["status"] == "pending"
    assert app_world["fake"].merge_calls == []
    assert app_world["opened"] == []


def test_filing_a_card_on_a_branchless_tree_is_refused(client, app_world, monkeypatch):
    """有活才有卡：绑定项目上树的分支不存在时，递卡当场被拒，错误信息点名
    该推哪条分支——而不是等到采纳时才发现无从交付。"""
    from app.domain.repository import forge_files

    pid = _make_project(client)
    tid = _make_topic(client, pid)

    async def missing_head(*args, **kwargs):
        return None

    monkeypatch.setattr(forge_files, "branch_head", missing_head)

    r = _make_card_response(client, tid)
    assert r.status_code == 422, r.text
    assert "没有可交付的改动" in r.json()["message"]
    assert _branch_of_record(client, tid) in r.json()["message"]
    assert _cards(client, tid) == []


def test_a_branch_holding_only_an_empty_commit_files_no_card(client, app_world):
    """一个不改任何文件的提交也没有东西可交：采纳它只会往主干并进一条空提交，
    还算作一次交付。递卡当场被拒，卡上不留一张。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    task = delivery_task(client, tid, commit=False)
    machine_commits(
        task.project_id, task.id, {}, "chore: open the card", allow_empty=True
    )

    r = _make_card_response(client, tid)
    assert r.status_code == 422, r.text
    assert "没有可交付的改动" in r.json()["message"]
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
    task_id = delivery_task_id(client, topic_id)

    async def read():
        from app.domain.room_task.models import Task

        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            return task.branch_name if task.status == "open" else None

    return client.portal.call(read)


def _branch_of_record(client, topic_id: str) -> str:

    return git_store.branch_for_task(delivery_task_id(client, topic_id))


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


def test_a_closed_unmerged_pr_stops_the_accept(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.close_unmerged(number)

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "关闭" in r.json()["message"]
    assert fake.merge_calls == []


# ---- 标题分家：仓库检查评判的那一行，必须就是落进历史的那一行 ----------------
#
# 平台按卡片 subject 写 PR 标题、按同一个 subject 落 squash 提交；仓库自己的检查
# 评判的是 PR 上那一行（编辑它就会重跑 `pull_request` 工作流）。谁在 GitHub 上把
# PR 标题改了 —— 或者卡片 subject 自己动了而 PR 没跟上 —— 两行就分家，再合下去，
# 写进历史的是一行仓库从没检查过的字。平台不判断哪一行才对（标题怎么写在仓库），
# 只确认这三个读者读的是同一个字符串（`pr_text.pr_title_matches_subject`）。


def test_accept_is_refused_when_the_pr_title_is_not_the_line_the_card_would_land(
    client, app_world
):
    """点采纳这一条路：PR 标题和卡片不是同一行就停下，一行都不合；把两行都告诉
    执行者，让他们从卡片这边改。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    # 有人在 GitHub 上把 PR 标题改了 —— 检查会按新标题重跑，但那不是卡片这行。
    fake.prs[number]["title"] = "fix(review): land the title the checks judged"

    r = _accept(client, cid)
    assert r.status_code == 422, r.text
    assert "标题" in r.json()["message"]
    assert fake.merge_calls == []  # 一行都没合下去
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_number"] == number  # PR 还挂在卡上：改完卡就能重试
    # 执行者是唯一该醒的人 —— 改卡会把 PR 标题一起改掉，检查按新标题重跑。
    settled = _task_settled(client, tid, "提交审阅时的标题不一致")
    assert "提交审阅时的标题不一致" in settled
    assert "fix(review): land the title the checks judged" in settled
    assert "chore(test): file an accept card" in settled


def test_accept_merges_again_once_the_card_says_the_line_the_pr_shows(
    client, app_world
):
    """停下之后唯一的那条出路：把**卡片**改成该落的那一行。`cheese_card(action=describe)` 会
    同时把 PR 标题改掉（`pr_publish.sync_pr_text`），两行又成同一个字符串，合并照
    常 —— 这道闸拦住的是「分家」，不是这一次合并本身。"""
    fake = app_world["fake"]
    _, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.prs[number]["title"] = "fix(review): land the title the checks judged"
    assert _accept(client, cid).status_code == 422

    r = _describe(
        client, tid, change_subject="fix(review): land the title the checks judged"
    )
    assert r.status_code == 200, r.text
    # 平台自己把 PR 标题改成了卡片那一行，不是反过来读回卡里。
    assert fake.prs[number]["title"] == "fix(review): land the title the checks judged"

    merged = _accept(client, cid)
    assert merged.status_code == 200, merged.text
    assert [m["number"] for m in fake.merge_calls] == [number]
    assert fake.merge_calls[0]["commit_title"].startswith(
        "fix(review): land the title the checks judged"
    )


def test_poll_holds_the_notice_and_the_armed_merge_while_the_titles_disagree(
    client, app_world
):
    """自动合并这条路上也一样：检查绿不绿和「该落哪一行」是两回事，标题分家就
    合不了。卡片转而提醒执行者，布防保持不动。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, auto_merge_allowed=True)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    assert _arm(client, cid, "alice").status_code == 200
    # 布防之后，标题在 GitHub 上被改了。
    fake.prs[number]["title"] = "fix(review): land the title the checks judged"

    _poll(client)
    wait_work_idle()

    assert fake.merge_calls == []  # 检查绿也不合
    settled = _task_settled(client, tid, "提交审阅时的标题不一致")
    assert "提交审阅时的标题不一致" in settled
    assert "fix(review): land the title the checks judged" in settled
    assert "chore(test): file an accept card" in settled
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"  # 没有以布防人的名义被合掉


def test_the_poll_clears_the_title_mismatch_face_once_the_two_lines_agree_again(
    client, app_world
):
    """两行又对上之后，卡面那行「合并已暂停」要跟着消失。

    改 PR 标题**不动 head**，所以换 head 时那次清理永远轮不到它：只有「还分不
    分家」这条判据自己收回，卡面才会空下来。不清，卡面一直亮着红灯，而同一
    次轮询已经在下边报了「可以合了」—— 同屏两句话互相打脸。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, auto_merge_allowed=True)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    # 先把两行拆开：轮询把这句写到卡面上。
    fake.prs[number]["title"] = "fix(review): land the title the checks judged"
    _poll(client)
    wait_work_idle()
    assert "不一致" in _cards(client, tid)[0]["note"]
    assert fake.merge_calls == []  # 检查绿也不合

    # 人把 GitHub 上的标题改回卡片那一行。**head 一个字没动**。
    fake.prs[number]["title"] = "chore(test): file an accept card"
    _poll(client)
    wait_work_idle()

    card = _cards(client, tid)[0]
    assert card["pr_head_sha"] == head_sha  # 这一轮 head 没动
    assert card["note"] == ""  # 那句说的是过去，跟着消失
    assert card["note_level"] is None


def test_merge_anyway_will_not_land_a_line_the_repository_never_checked(
    client, app_world
):
    """「放行」是一条能盖过检查的路，但它盖不过这件事：别人没评判过的那一行，谁
    点头也不该被写进历史。要求还是同一条 —— 从卡片这边改成该落的那一行。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.prs[number]["title"] = "fix(review): land the title the checks judged"

    r = _merge_anyway(client, cid, "alice", reason="我看过了")
    assert r.status_code == 422, r.text
    assert "标题" in r.json()["message"]
    assert fake.merge_calls == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"


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


def test_accept_without_forge_binding_refuses_delivery(client, app_world, monkeypatch):
    """A missing authoritative repository must never become a local merge."""
    from app.domain.project import forge as project_forge

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    async def missing_binding(*args, **kwargs):
        return None

    with monkeypatch.context() as unavailable:
        unavailable.setattr(project_forge, "binding_for_project", missing_binding)
        r = _accept(client, cid)
    assert r.status_code == 422, r.text
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_number"] is None
    assert app_world["fake"].merge_calls == []
    delivered = _topic(client, tid)
    assert delivered["status"] == "active"
    assert (
        client.get(f"/topics/{delivery_task_id(client, tid)}/task").json()["data"][
            "accepted_at"
        ]
        is None
    )
    assert delivered["accepted_at"] is None


def test_existing_pr_cannot_fall_back_to_local_merge_when_binding_disappears(
    client, app_world, monkeypatch
):
    from app.domain.project import forge as project_forge

    _pid, tid, cid, _number, _head = _ready_card(client, app_world)

    async def unavailable(*_):
        return None

    monkeypatch.setattr(project_forge, "github_app_tokens_for_project", unavailable)
    response = _accept(client, cid)
    assert response.status_code == 422, response.text
    assert app_world["fake"].merge_calls == []
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["forge"]["reports_checks"] is True


def test_another_forge_owns_its_checks_and_accept_operation(
    client, app_world, monkeypatch
):
    """A provider without GitHub must not fall through to the platform merge."""
    from app.core.errors import ValidationError
    from app.domain.review import forge as forge_mod
    from app.domain.review.models import AcceptStatus

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    merges = []

    class AnotherForge:
        kind = forge_mod.ForgeKind.github_app
        declaration = ""
        checks_passed = False
        capabilities = forge_mod.ForgeCapabilities(
            reports_checks=True,
            hosts_proposals=True,
            can_write_remote=True,
            has_external_remote=True,
            pushes_to_external_remote=True,
            identity=forge_mod.ForgeIdentity.user,
        )

        async def accept(self, service, card, topic, decided_by, *, seen_head):
            if not self.checks_passed:
                raise ValidationError("Provider checks are still running")
            merges.append((card.id, decided_by, seen_head))
            card.status = AcceptStatus.accepted
            return card

    provider = AnotherForge()

    async def resolve(**_):
        return provider

    monkeypatch.setattr(forge_mod, "resolve", resolve)
    card = _cards(client, tid)[0]
    assert card["forge"]["reports_checks"] is True
    assert card["merge_state"]["state"] == "unknown"
    blocked = _accept(client, cid)
    assert blocked.status_code == 422, blocked.text
    assert "Provider checks are still running" in blocked.text
    assert merges == []
    provider.checks_passed = True
    accepted = _accept(client, cid)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "accepted"
    assert merges == [(_uuid.UUID(cid), "alice", None)]


@pytest.mark.parametrize("override", [False, True])
def test_another_forge_refreshes_polls_and_merges_the_viewed_revision(
    client, app_world, monkeypatch, override
):
    """Provider dispatch retains the common actor and revision guards."""
    from app.core.errors import ValidationError
    from app.domain.review import forge as forge_mod
    from app.domain.review.models import AcceptStatus
    from app.domain.review.repositories import AcceptCardRepository

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    calls = []
    head = "c" * 40

    async def attach_proposal():
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(_uuid.UUID(cid))
            card.pr_number = 42
            card.pr_url = "https://forge.example/proposals/42"
            await session.commit()

    asyncio.run(attach_proposal())

    class AnotherForge(forge_mod.Forge):
        kind = forge_mod.ForgeKind.github_app
        declaration = ""

        @classmethod
        def serves(cls, capabilities):
            return True

        async def refresh_unseen_head(self, service, card, topic, action):
            calls.append("refresh")
            raise ValidationError("Provider revision has not been displayed")

        async def poll(self, service, card, topic, **_):
            calls.append("poll")
            card.pr_head_sha = head
            card.merge_state = {
                "state": "clean",
                "who": "human",
                "reasons": [],
                "head_sha": head,
                "checked_at": None,
                "since": None,
            }

        async def accept(self, service, card, topic, decided_by, *, seen_head):
            calls.append(("accept", decided_by, seen_head))
            card.status = AcceptStatus.accepted
            return card

        async def merge_despite_checks(
            self, service, card, topic, decided_by, *, seen_head, reason
        ):
            calls.append(("override", decided_by, seen_head, reason))
            card.status = AcceptStatus.accepted
            return card

    async def resolve(**_):
        return AnotherForge(
            forge_mod.ForgeCapabilities(
                reports_checks=True,
                hosts_proposals=True,
                can_write_remote=True,
                has_external_remote=True,
                pushes_to_external_remote=True,
                identity=forge_mod.ForgeIdentity.user,
            )
        )

    monkeypatch.setattr(forge_mod, "resolve", resolve)
    unseen = _accept(client, cid, head_sha=None)
    assert unseen.status_code == 422, unseen.text
    assert "Provider revision has not been displayed" in unseen.text
    assert calls == ["refresh"]
    assert _poll(client)["cards_checked"] == 1
    card = _cards(client, tid)[0]
    assert card["merge_state"]["state"] == "clean"
    assert card["merge_state"]["head_sha"] == head
    assert calls == ["refresh", "poll"]
    denied = _accept(client, cid, handle="bob", head_sha=head)
    assert denied.status_code == 403, denied.text
    stale = _accept(client, cid, head_sha="d" * 40)
    assert stale.status_code == 422, stale.text
    assert calls == ["refresh", "poll"]
    if override:
        result = _merge_anyway(client, cid, "alice", reason="Reviewed", head_sha=head)
        expected = ("override", "alice", head, "Reviewed")
    else:
        result = _accept(client, cid, head_sha=head)
        expected = ("accept", "alice", head)
    assert result.status_code == 200, result.text
    assert result.json()["data"]["status"] == "accepted"
    assert calls == ["refresh", "poll", expected]


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
    # 没配必跑名单，GitHub 说 unstable 就是「可以合」；还在跑的那个检查不挡
    # 采纳，下一步在验收人手上（`whose_move` 的 unstable 那一档）。
    assert mirror["who"] == "human"
    assert mirror["head_sha"] == head_sha

    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _poll(client)
    mirror = _cards(client, tid)[0]["merge_state"]
    assert mirror["state"] == "clean"
    assert mirror["who"] == "human"


def test_a_running_check_outside_the_required_list_still_waits_on_the_reviewer(
    client, app_world
):
    """必跑检查都绿了、只剩一条非必跑检查还在跑：卡可以采纳，验收人那行要亮黄灯。

    GitHub 对这种情况报 unstable（可采纳），但 check runs 里还有 in_progress。
    `whose_move` 若按「有检查在跑」把这一步交给 ci，`awaits_me` 就是 false，侧栏
    那盏灯停在蓝色 —— 2026-10-21 的 #4562 正是这样：两个 job 的每一步都绿了、run
    也结束了，GitHub 却一直把它们报成 in_progress。
    """
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.mergeable_state_by_number[number] = "unstable"
    fake.check_runs_by_sha[head_sha] = [
        {"name": "test", "status": "completed", "conclusion": "success"},
        {"name": "scope", "status": "in_progress", "conclusion": None},
    ]

    _poll(client)

    card = _cards(client, tid)[0]
    assert card["merge_state"]["state"] == "unstable"
    assert card["merge_state"]["who"] == "human"

    row = {
        item["id"]: item
        for item in client.get(
            f"/projects/{pid}/tasks", headers=session_auth_headers("alice")
        ).json()["data"]["data"]
    }[str(delivery_task_id(client, tid))]
    assert row["presentation"] == {"column": "needs_you", "phrase": "awaiting_review"}
    assert row["awaits_me"] is True


def test_poll_never_merges_an_unarmed_card(client, app_world):
    """采纳是人的点击；轮询器自己绝不替人合未布防的卡 —— #414 的反面。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    _poll(client)
    _poll(client)

    assert fake.merge_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"


def _card_events(client, card_id):
    """Read the card's attributed events, including expanded failure details."""
    from sqlalchemy import select

    from app.domain.block.models import Block
    from app.domain.review.models import AcceptCard

    async def read():
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, uuid.UUID(card_id))
            blocks = list(
                await session.scalars(
                    select(Block).where(Block.conversation_id == card.task_id)
                )
            )
            return "\n".join(
                f"{b.content}\n{(b.meta or {}).get('detail', '')}" for b in blocks
            )

    return asyncio.run(read())


def test_poll_clean_notifies_the_reviewer_once_per_head(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    _poll(client)
    text = _card_events(client, cid)
    assert "等 <@alice> 采纳" in text
    first = text.count("等 <@alice> 采纳")

    _poll(client)  # 同一个 head：不重复
    wait_work_idle()
    assert _card_events(client, cid).count("等 <@alice> 采纳") == first

    new_sha = fake.push_new_commit(number)  # 新 head 转绿是新事实
    fake.check_state_by_sha[new_sha] = ("success", "全绿")
    _poll(client)
    _poll(client)
    import time

    text = _card_events(client, cid)
    for _ in range(40):
        if text.count("等 <@alice> 采纳") >= first + 1:
            break
        time.sleep(0.05)
        text = _card_events(client, cid)
    assert text.count("等 <@alice> 采纳") == first + 1


def test_poll_red_checks_nudge_cheese_once_with_the_logs(client, app_world, stub_hooks):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("failure", "pytest: 3 failed")

    _poll(client)
    wait_work_idle()
    contents = _card_events(client, cid)
    assert "pytest: 3 failed" in contents
    from sqlalchemy import select

    from app.domain.delivery.models import Delivery
    from app.domain.review.models import AcceptCard

    async def pending_instruction():
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, uuid.UUID(cid))
            row = await session.scalar(
                select(Delivery).where(Delivery.conversation_id == card.task_id)
            )
            assert row.state == "pending"
            return row.payload["content"]

    prompt = asyncio.run(pending_instruction())
    assert "gh api repos/" in prompt
    assert f"repos/{REPO}/actions/jobs/" in prompt
    nudge_count = contents.count("pytest: 3 failed")

    _poll(client)  # 同一个失败：不重复
    wait_work_idle()
    assert _card_events(client, cid).count("pytest: 3 failed") == nudge_count

    # 新提交上同样的失败是新事实。
    new_sha = fake.push_new_commit(number)
    fake.check_state_by_sha[new_sha] = ("failure", "pytest: 1 failed now")
    _poll(client)
    wait_work_idle()
    assert "pytest: 1 failed now" in _card_events(client, cid)


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
    assert "作废" in _card_events(client, cid)

    # 没有可作废的东西时，head 移动不打扰任何人。
    before = _card_events(client, cid).count("作废")
    fake.push_new_commit(number)
    _poll(client)
    wait_work_idle()
    assert _card_events(client, cid).count("作废") == before


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


def test_the_grace_page_is_rung_once_not_once_a_poll(client, app_world):
    """宽限期那句「转人」只说一次，卡面上还压着别的理由也不行。

    一张卡可能同时摊上两件事：必须跑的检查一直没报到（超时转人），PR 标题又和卡上
    不一致（另一条待发）。卡面那一行要是每拍都被后者重写一遍，前者按整行文字做的
    那次去重就永远认不出自己说过 —— 验收人每 60 秒被重新叫一次，一直叫到那张卡
    不再等为止。
    """
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, required_checks=[{"name": "test"}])
    fake.check_runs_by_sha[head_sha] = []  # 必须跑的检查从未报到
    fake.prs[number]["title"] = "a line the card did not write"  # 标题也和卡上分家

    _poll(client)
    _set_merge_since(client, cid, "2020-01-01T00:00:00+00:00")

    said = []
    for _ in range(3):
        _poll(client)
        wait_work_idle()
        said.append(_room_event_count(client, tid, "merge_withheld"))

    assert said == [1, 1, 1]


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


def test_a_pr_closed_without_merging_voids_its_card(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.close_unmerged(number)

    result = _poll(client)
    assert result["errors"] == []
    card = _cards(client, tid)[0]
    assert card["status"] == "revoked"
    assert f"PR #{number} 已在 GitHub 关闭且没有合并" in card["note"]
    assert "作废" in card["note"]
    assert fake.merge_calls == []

    fake.status_calls.clear()
    assert _poll(client)["cards_checked"] == 0
    assert fake.status_calls == []  # a voided card is not followed any more
    assert _cards(client, tid)[0]["note"] == card["note"]


def test_a_task_can_be_submitted_again_after_its_pr_was_closed(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.close_unmerged(number)
    _poll(client)

    response = _make_card_response(client, tid)

    assert response.status_code == 200, response.text
    fresh = response.json()["data"]
    assert fresh["id"] != cid
    assert fresh["status"] == "pending"
    statuses = {card["id"]: card["status"] for card in _cards(client, tid)}
    assert statuses == {cid: "revoked", fresh["id"]: "pending"}


def _set_task_pr(client, topic_id: str, number: int) -> None:
    """任务行上已经记着某个 PR —— 生产上由开 PR 的那次发布写上去。"""
    from app.domain.room_task.models import Task

    task_id = delivery_task_id(client, topic_id)

    async def _do() -> None:
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            task.pr_number = number
            task.pr_url = f"https://github.com/{REPO}/pull/{number}"
            await session.commit()

    asyncio.run(_do())


def _publish_the_card(client, card_id: str, topic_id: str, project_id: str) -> int:
    """递卡时那段 fire-and-forget 的发布，在这里同步跑一遍（`pr_publish._run`
    的两步：开/认领这条分支的 PR，再把号写回卡和任务）。"""
    from app.domain.review import pr_publish

    async def _do() -> int:
        async with client.test_request_factory() as session:
            pr = await pr_publish.open_pr_for_card(
                session,
                card_id=_uuid.UUID(card_id),
                topic_id=_uuid.UUID(topic_id),
                project_id=_uuid.UUID(project_id),
            )
        assert pr is not None
        await pr_publish.record_pr(
            client.test_request_factory, card_id=_uuid.UUID(card_id), pr=pr
        )
        return int(pr["number"])

    return client.portal.call(_do)


def test_a_card_does_not_ride_a_closed_pr_the_task_still_names(client, app_world):
    """任务还指着一条已关闭的 PR 时递卡：卡不抄那个号，更不会几秒后被作废。

    复现 2026-10-22 00:55（任务 9bd0b806）：旧草稿 PR 被人关掉、分支又推了新
    提交，平台为新的 head 另开一条 PR —— 但那个新号要等递卡之后的那次发布才写
    回来。递卡正落在这一刻：卡把任务上那个旧号抄了过去，轮询器一跳就按「PR 已
    关闭」把刚递上来的卡作废，新号晚一步才写回一张已经作废的卡。
    """
    fake = app_world["fake"]
    pid, tid = _room_with_work(client)
    branch = _disk_branch(client, tid)
    # 旧草稿 PR 曾经开着，被人在 GitHub 上关掉；任务行还挂着它的号。
    fake.seed_pr(7, head=branch, title="chore(test): file an accept card")
    _set_task_pr(client, tid, 7)
    fake.close_unmerged(7)

    cid = _make_card(client, tid)

    # 这个号不是这次交付的 PR：卡不继承它，等发布把新 PR 写回来。
    assert _cards(client, tid)[0]["pr_number"] is None

    # 轮询一跳：不能因为任务上那条旧的已关闭 PR 就把新卡作废。
    _poll(client)
    assert _cards(client, tid)[0]["status"] == "pending"

    # 递卡之后那次发布开出新 PR 并写回卡和任务：卡骑的是它，不是旧的 #7。
    number = _publish_the_card(client, cid, tid, pid)
    assert number != 7
    _poll(client)
    card = _cards(client, tid)[0]
    assert card["status"] == "pending"
    assert card["pr_number"] == number


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
    assert (
        client.get(f"/topics/{delivery_task_id(client, tid)}/task").json()["data"][
            "accepted_at"
        ]
        is not None
    )
    assert delivered["accepted_at"] is None


def test_poll_records_who_merged_a_pr_on_github(client, app_world):
    """GitHub 上直接合掉的 PR：卡上留下那个账号，总览那一行才有人可显示。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.merge_externally(number, merged_by={"github_id": "4242", "login": "octocat"})

    _poll(client)

    assert _stored_merged_by(client, cid) == {
        "github_id": "4242",
        "login": "octocat",
    }


def test_poll_records_a_card_merged_by_nobody_named(client, app_world):
    """问过了、GitHub 没报人：记 `{}`，补录不再反复问同一张卡。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.merge_externally(number)

    _poll(client)

    assert _stored_merged_by(client, cid) == {}


def test_backfill_records_who_merged_a_card_merged_before_the_column(client, app_world):
    """存量卡：补录扫到 14 天内、没有平台采纳人的卡，读一次 GitHub 补上。"""
    from app.domain.review.models import AcceptStatus

    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.merge_externally(number, merged_by={"github_id": "77", "login": "old-octo"})

    async def _as_already_merged():
        from app.domain.review.repositories import AcceptCardRepository

        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(_uuid.UUID(cid))
            assert card is not None
            card.status = AcceptStatus.accepted
            card.pr_merged_at = datetime.now(UTC)
            card.decided_by = None
            card.merged_by = None
            await session.commit()

    asyncio.run(_as_already_merged())

    result = _backfill_merge_actors(client)

    assert result["filled"] == 1
    assert _stored_merged_by(client, cid) == {
        "github_id": "77",
        "login": "old-octo",
    }
    # 已有答案的卡不再问第二次。
    assert _backfill_merge_actors(client)["cards_checked"] == 0


def test_external_merge_closes_a_returned_batch_without_rewriting_its_review(
    client, app_world
):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    branch = _room_open_tree_branch(client, tid)
    response = client.post(
        f"/accept-cards/{cid}/reject",
        json={"decided_by": "alice", "note": "The batch boundary is not proven"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    rejected = _cards(client, tid)[0]
    delivered_head = fake.push_new_commit(number)
    fake.merge_externally(number, merge_commit_sha="a34b8e12")

    result = _poll(client)

    assert result["cards_checked"] == 1
    assert result["errors"] == []
    card = _cards(client, tid)[0]
    assert card["status"] == "rejected"
    assert card["note"] == rejected["note"]
    assert card["decided_by"] == rejected["decided_by"]
    assert card["pr_head_sha"] == rejected["pr_head_sha"]
    assert card["pr_merged_at"] is not None
    # The task closes once its AI teammate has written it up.
    written_up(client, delivery_task_id(client, tid))
    assert _room_open_tree_branch(client, tid) is None
    assert _branch_of_record(client, tid) == branch
    assert fake.merge_calls == []
    room = _task_settled(client, tid, "原退回记录保留")
    assert "原退回记录保留" in room
    assert "本地同步待补" not in room

    async def check_delivery_boundary():
        from app.domain.review.repositories import AcceptCardRepository
        from app.domain.room_task.repositories import TaskRepository

        async with client.test_factory() as session:
            stored = await AcceptCardRepository(session).get(_uuid.UUID(cid))
            tree = await TaskRepository(session).get(stored.task_id)
            assert tree.status == "closed"
            assert tree.delivered_head == delivered_head

    asyncio.run(check_delivery_boundary())
    assert _poll(client)["cards_checked"] == 0


def test_the_merge_that_closed_a_task_does_not_close_it_again(client, app_world):
    """一次交付只落地一次：已经合过的那一笔不会再关一次重开出来的任务。

    任务 #58（2026-10-11）：同一张 PR 上有一张退回过的卡。最后一步被采纳、合
    并落地、任务关掉，负责人重开去接着做（新分支，`pr_number` 归 None）。下一
    跳轮询又把那张退回卡读了一遍 —— 它自己从没结算过，而 PR 已经合了 —— 于是
    同一笔合并第二次落到这个任务上，它一分钟后又进了一次写总结的窗口。开着却
    写「已采纳」的任务谁都不收：左侧栏要 `closing_since` 为空，「未完成」要
    presentation 不是 done，「已关闭」要 status 是 closed（2026-10-11，任务 #58）。"""
    fake = app_world["fake"]
    pid, tid, returned, number, head_sha = _ready_card(client, app_world)
    rejected = client.post(
        f"/accept-cards/{returned}/reject",
        json={"decided_by": "alice", "note": "这批的边界还没证明"},
        headers=session_auth_headers("alice"),
    )
    assert rejected.status_code == 200, rejected.text
    # 退回的那一轮（告诉队友去改）先跑完：生产上改完再交时它早就结束了。
    wait_work_idle()
    # 同一张 PR 上再递一张 —— 生产上这是改完又交的那一版。
    resent = _make_card(client, tid)
    _give_card_a_pr(client, app_world, tid, resent, number)
    fake.check_state_by_sha[head_sha] = ("success", "全部通过")
    fake.merge_sha_by_number[number] = "merge-sha-1"
    accepted = _accept(client, resent)
    assert accepted.status_code == 200, accepted.text
    task_id = delivery_task_id(client, tid)
    written_up(client, task_id)
    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "closed"

    reopened = client.post(
        f"/topics/{task_id}/reopen",
        headers=session_auth_headers(task["owner_handle"]),
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["pr_number"] is None

    result = _poll(client)

    assert result["errors"] == []
    cards = {card["id"]: card for card in _cards(client, tid)}
    # 这张退回卡自己从没结算过，所以它还在这条轮询路径上 —— 下面这一条就是
    # 「这一跳真的读了那张已经合掉的 PR」的证据。
    assert cards[returned]["status"] == "rejected"
    assert cards[returned]["pr_merged_at"] is not None
    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "open"
    assert task["presentation"]["column"] != "done"


@pytest.mark.parametrize("closed", [False, True])
def test_poll_never_merges_or_rewrites_a_returned_pr(client, app_world, closed):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    branch = _room_open_tree_branch(client, tid)
    response = client.post(
        f"/accept-cards/{cid}/reject",
        json={"decided_by": "alice", "note": "Needs another review"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    rejected = _cards(client, tid)[0]
    fake.check_state_by_sha[head_sha] = ("success", "All checks passed")
    if closed:
        fake.close_unmerged(number)

    assert _poll(client)["errors"] == []

    card = _cards(client, tid)[0]
    assert card["status"] == "rejected"
    assert card["note"] == rejected["note"]
    assert card["pr_merged_at"] is None
    assert _room_open_tree_branch(client, tid) == branch
    assert fake.merge_calls == []


def test_poll_prefers_a_resubmitted_card_to_its_previous_return(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    response = client.post(
        f"/accept-cards/{cid}/reject",
        json={"decided_by": "alice", "note": "Needs another review"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    new_card = _make_card(client, tid)
    _give_card_a_pr(client, app_world, tid, new_card, number)
    fake.merge_externally(number)

    result = _poll(client)

    assert result["cards_checked"] == 1
    assert result["errors"] == []
    cards = {card["id"]: card for card in _cards(client, tid)}
    assert cards[new_card]["status"] == "accepted"
    assert cards[cid]["status"] == "rejected"


def test_poll_steady_state_costs_one_pr_read_per_tick(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "还在跑")

    _poll(client)
    _poll(client)

    assert fake.status_calls == [number, number]
    assert fake.head_sha_calls == []


# ==================== 读卡顺手补上过期的合并态快照 ===========================
#
# 卡面上的 `merge_state` 是一份快照，而前端那颗「采纳」按钮按它亮不亮
# (`TopicAcceptCard.vue` 的 `acceptBlockedTitle`)。界面每 15s 来读一次这条
# 路，读到的却可能是轮询器几分钟前写下的旧状态 —— 于是「检查都通过了，采纳
# 按钮点不动」。实测：CI 11:28Z 就全绿，卡到 11:49Z 才写进 `clean`；另一次卡
# 在 12:06Z 被写成 `unknown`（GitHub 那一刻还没算完 mergeable），而 `/pr-checks`
# 早已 `mergeable: true`，那份快照又冻了五分钟。**界面的 15s 轮询自己收敛不了**
# —— 它每次读回来的都是同一份旧快照。所以读卡这条路自己把过期的补上，这一节
# 测的就是它，以及它必须停在哪。


def _stale_mirror_iso(
    client, card_id: str, *, minutes: int = 10, state: str = "unknown"
):
    """把卡面上的快照做成「几分钟前算的、当时还没结论」的样子（GitHub 还没算完
    mergeable 时 `merge_state._passthrough` 写下的就是这一份），返回它那个时刻。"""
    from app.domain.review.repositories import AcceptCardRepository

    checked_at = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()

    async def _do() -> None:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(_uuid.UUID(card_id))
            assert card is not None
            card.merge_state = {
                **(card.merge_state or {}),
                "state": state,
                "who": "platform",
                "head_sha": card.pr_head_sha,
                "checked_at": checked_at,
                "since": checked_at,
            }
            await s.commit()

    asyncio.run(_do())
    return checked_at


def _archive_topic(client, topic_id: str) -> None:
    from app.domain.topic.models import TopicStatus
    from app.domain.topic.repositories import TopicRepository

    async def _do() -> None:
        async with client.test_factory() as s:
            topic = await TopicRepository(s).get(_uuid.UUID(topic_id))
            assert topic is not None
            topic.status = TopicStatus.archived
            await s.commit()

    asyncio.run(_do())


def test_reading_a_card_refreshes_a_stale_merge_snapshot(client, app_world):
    """复现并锁死「检查全绿、按钮点不动」：库里那份是过期的 `unknown`，读一次
    卡就该把它补成此刻的 `clean` —— 界面拿到的正是这个 payload。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    stale_at = _stale_mirror_iso(client, cid)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")  # GitHub 那边早就绿了

    mirror = _cards(client, tid)[0]["merge_state"]

    assert mirror["state"] == "clean"
    assert mirror["who"] == "human"
    assert mirror["checked_at"] > stale_at
    assert fake.status_calls == [number]


def test_reading_a_card_leaves_a_fresh_snapshot_alone(client, app_world):
    """地板：读卡是热点，不能每个读者都替全平台去问一次 GitHub。一份刚算过的
    快照，读多少次都不会再多一次外呼。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("pending", "还在跑")

    _poll(client)  # 轮询器写下新的一份，`checked_at` 就是此刻
    assert fake.status_calls == [number]

    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    assert _cards(client, tid)[0]["merge_state"]["state"] == "unstable"

    assert fake.status_calls == [number]  # 读卡没有再加一次


def test_reading_never_moves_the_head_of_a_card_that_moved_on(client, app_world):
    """head 动了是**轮询器**的状态迁移（`_poll_pr_card` → `dismiss_stale_accept`
    会撤掉别人给的批准、发通知），不是一次读卡该顺手干的事：浏览器每 15s 读一
    次，读的人可能根本不在乎这张卡。读只补过期的那一份，不动 head。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _stale_mirror_iso(client, cid)
    fake.push_new_commit(number)

    card = _cards(client, tid)[0]

    assert card["pr_head_sha"] == head_sha  # 没跟着 GitHub 往前跑
    assert card["merge_state"]["state"] == "unknown"  # 快照原样留着，等轮询器
    assert fake.status_calls == [number]  # 问过一次才知道 head 动了


def test_reading_an_archived_topics_card_never_polls_github(client, app_world):
    """归档的房间不会再有人采纳这张卡 —— 不为它花一次外呼。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _stale_mirror_iso(client, cid)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    _archive_topic(client, tid)

    assert _cards(client, tid)[0]["merge_state"]["state"] == "unknown"
    assert fake.status_calls == []


def test_a_github_hiccup_on_a_read_never_breaks_the_card_list(client, app_world):
    """读卡不能因为 GitHub 抖一下就 500：陈旧的那份照样发出去，下一次再补。"""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _stale_mirror_iso(client, cid)
    fake.status_error = RuntimeError("github is down")

    r = client.get(f"/topics/{tid}/accept-card")

    assert r.status_code == 200
    assert r.json()["data"]["data"][0]["merge_state"]["state"] == "unknown"


def test_a_slow_github_never_holds_a_card_read_and_its_answer_still_lands(
    client, app_world, monkeypatch
):
    """读卡等 GitHub 有个头：等不到就先把手上那份发出去，那一次重算接着在后台做
    完、写回卡上，下一次读就是新的。房间里每个开着的页面每 15s 读一次卡，它们
    一起等的是同一次重算，不是各问一遍 —— 一张接一张地等 GitHub，一次读卡曾经
    要 40 秒，排在它后面的请求全被拖住。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "accept_pr_snapshot_read_wait_s", 0.05)
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _stale_mirror_iso(client, cid)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    fake.status_delay_s = 1.0

    began = time.monotonic()
    first = _cards(client, tid)[0]["merge_state"]["state"]
    assert time.monotonic() - began < 0.8
    assert first == "unknown"

    deadline = time.monotonic() + 10
    while _cards(client, tid)[0]["merge_state"]["state"] != "clean":
        assert time.monotonic() < deadline, "the refresh never landed"
        time.sleep(0.05)
    assert fake.status_calls == [number]


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
        client,
        cid,
        headers={
            "Authorization": f"Bearer {mint_scoped_token(project_id=pid, topic_id=tid)}"
        },
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
    assert (
        client.get(f"/topics/{delivery_task_id(client, tid)}/task").json()["data"][
            "accepted_at"
        ]
        is not None
    )
    assert delivered["accepted_at"] is None
    # 署名：谁、理由、以及合并那一刻检查到底是什么状态。
    assert "alice" in card["note"]
    assert "CI runner 挂了" in card["note"]
    assert "明知检查未全绿仍合并" in card["note"]


def test_merge_anyway_admission_follows_the_override_roster(client, app_world):
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    _protect(client, pid, override_handles=["carol"])
    fake.check_state_by_sha[head_sha] = ("failure", "红")
    # carol is named by the project's override roster, and she is on the project
    # too: the roster decides *which* participant may force the merge, and room
    # membership decides whether the caller is a participant at all
    # (`_card_actor`, 2026-09-26). Both questions are asked; this case is about
    # the first one.
    join_project_team(client, pid, "carol")

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

    repo_path = git_store.ensure_repo(project_id)
    branch = git_store.branch_for_task(topic_id)
    return subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", branch],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_push_fix_observes_machine_push_and_disarms_old_approval(client, app_world):
    """The executor pushes; refreshing the card cannot approve the new revision."""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    puid, tuid = _uuid.UUID(pid), delivery_task_id(client, tid)

    machine_commits(puid, tuid, {"work.txt": "first pass\n"})
    first_head = _real_git_head(puid, tuid)
    fake.prs[number]["head_sha"] = first_head

    _poll(client)
    card = _cards(client, tid)[0]
    assert card["pr_head_sha"] == first_head
    _protect(client, pid, auto_merge_allowed=True)
    assert _arm(client, cid, "alice").status_code == 200
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] == "alice"

    machine_commits(puid, tuid, {"work.txt": "fixed\n"})
    second_head = _real_git_head(puid, tuid)
    fake.prs[number]["head_sha"] = second_head
    pushed = client.post(
        f"/topics/{delivery_task_id(client, tid)}/push-fix",
        headers=session_auth_headers("alice"),
    ).json()["data"]
    assert pushed["pushed"] is True
    assert second_head != first_head
    assert _cards(client, tid)[0]["pr_head_sha"] == second_head
    assert _cards(client, tid)[0]["auto_merge"]["armed_by"] is None
    assert fake.merge_calls == []

    again = client.post(
        f"/topics/{delivery_task_id(client, tid)}/push-fix",
        headers=session_auth_headers("alice"),
    ).json()["data"]
    assert again["pushed"] is False, "没有新东西可推时,再问一次不算错误"


@pytest.mark.parametrize("confirmed", [True, False])
def test_push_fix_drops_dependency_only_after_forge_confirms_target(
    client, app_world, monkeypatch, confirmed
):
    from app.domain.room_task.models import Task, TaskStatus

    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    task_id = delivery_task_id(client, tid)

    async def dependency():
        async with client.test_factory() as session:
            parent = Task(
                project_id=_uuid.UUID(pid),
                room_id=_uuid.UUID(tid),
                title="Rejected parent",
                branch_name="task/parent",
                base_branch="main",
                status=TaskStatus.closed,
            )
            session.add(parent)
            await session.flush()
            child = await session.get(Task, task_id)
            child.base_task_id = parent.id
            child.base_branch = parent.branch_name
            child.pr_number = number
            child.pr_url = f"https://github.com/{REPO}/pull/{number}"
            await session.commit()

    asyncio.run(dependency())
    fake.prs[number]["base"] = "task/parent"
    _protect(client, pid, auto_merge_allowed=True)
    assert _approve(client, cid, "alice").status_code == 200
    assert _arm(client, cid, "alice").status_code == 200
    if not confirmed:
        original = fake.pull_request_status

        async def stale_status(**kwargs):
            status = await original(**kwargs)
            from dataclasses import replace

            return replace(status, base_ref="task/parent")

        monkeypatch.setattr(fake, "pull_request_status", stale_status)

    endpoint = f"/topics/{task_id}/push-fix?drop_dependency=true"
    response = client.post(endpoint, headers=session_auth_headers("alice"))
    assert response.status_code == (200 if confirmed else 422), response.text
    card = _cards(client, tid)[0]
    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert len(_cards(client, tid)) == 1
    assert card["id"] == cid
    assert fake.merge_calls == []
    if confirmed:
        assert response.json()["data"]["pushed"] is True
        assert fake.prs[number]["base"] == "main"
        assert task["base_task_id"] is None
        assert task["base_branch"] == "main"
        assert card["approvals"] == []
        assert card["auto_merge"]["armed_by"] is None
        assert card["pr_head_sha"] == head_sha
        again = client.post(endpoint, headers=session_auth_headers("alice"))
        assert again.json()["data"]["pushed"] is False
    else:
        assert "仓库尚未确认新的目标分支" in response.json()["message"]
        assert task["base_task_id"] is not None
        assert task["base_branch"] == "task/parent"
        assert card["approvals"]
        assert card["auto_merge"]["armed_by"] == "alice"


def test_push_fix_reports_unreachable_forge_without_pushing(client, app_world):
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    app_world["fake"].status_error = github_pr.GitHubPrError("HTTP 502")
    pushed = client.post(
        f"/topics/{delivery_task_id(client, tid)}/push-fix",
        headers=session_auth_headers("alice"),
    ).json()["data"]

    assert pushed["pushed"] is False
    assert "HTTP 502" in pushed["reason"]
    card = _cards(client, tid)[0]
    assert "HTTP 502" in card["note"]
    assert card["note_level"] == "error"
    assert card["pr_head_sha"] == head_sha


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
    assert _cards(client, tid)[0]["status"] == "pending"


def test_remote_merge_records_acceptance(client, app_world):
    """A successful forge merge records the card as accepted."""
    fake = app_world["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, app_world)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert "本地同步待补" not in card["note"]


# ============================ 连续交付 ======================================
#
# 一个房间交付完不会停下来。上一批采纳合并之后，下一行代码马上就写出来了 —— 而
# 这正是 2026-09-08 在本仓库自己的房间里踩到的那一刀：卡 accepted、树 merged、
# PR 被 squash 成 main 上的一个提交，而房间的工作区还停在那条已经交付完的分支
# 上，所以「继续干活」写出来的每一个提交都落在一条 main 里再也到不了的分支上。
# 下面这一组走的是真入口：真的 accept、真的 git 提交，然后问 git。


def _disk_branch(client, place_id: str) -> str:
    """磁盘这一层说的「这个地方现在写哪条分支」——AI commit 时用的就是它。"""

    return git_store.branch_for_task(delivery_task_id(client, place_id))


def _branch_holds(project_id: str, branch: str, sha: str) -> bool:

    done = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, branch],
        cwd=git_store.ensure_repo(_uuid.UUID(project_id)),
        capture_output=True,
        text=True,
    )
    return done.returncode == 0


# ===================== 有东西就有 PR / cheese ready ==========================
#
# #718 拍板①：draft PR 在这批活第一次提交时就开，`cheese ready` 只是把 draft
# 翻成 ready。平台看不见容器里那次 `git commit`，所以「第一次提交」是**观察**
# 出来的：巡检看到这一批的分支比 main 多了东西。
#
# 真实仓库上实测过这两个 GitHub 能力（本仓、2026-09-08、用平台 App 的 token）：
# `POST /pulls` 带 draft=true 开出 PR #738 且 `"draft": true`；GraphQL
# `markPullRequestReadyForReview` 把它翻回 `isDraft: false`。


def _sweep(client) -> dict:
    """每 300 秒那一拍：只扫活动窗口里动过的活。"""
    from app.core.config import settings
    from app.domain.review import pr_publish

    return client.portal.call(
        lambda: pr_publish.sweep_draft_prs(
            client.test_request_factory,
            announce=announce,
            record_instruction=record_task_instruction,
            quiet_window_s=settings.draft_pr_sweep_activity_window_s,
        )
    )


def _room_with_work(client) -> tuple[str, str]:
    """一个房间，派了一条活（这是房间开出一批活的那道门），并且提交了东西。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    delivery_task(client, tid, commit=False)
    machine_commits(_uuid.UUID(pid), delivery_task_id(client, tid), {"a.txt": "work\n"})
    return pid, tid


def _ready(client, topic_id: str):
    return client.post(
        f"/topics/{delivery_task_id(client, topic_id)}/ready",
        headers=delivery_headers(client, topic_id),
    )


@pytest.fixture
def sweeping(app_world, monkeypatch):
    """巡检开着（默认它靠 App 配置判断在不在线，测试环境里没配）。"""
    from app.domain.review import pr_publish

    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    return app_world


def test_a_batch_with_commits_gets_a_draft_pr_without_anybody_filing_a_card(
    client, sweeping
):
    """有东西就有 PR：没有人递卡，光是提交了东西，PR 就该在了 —— 而且是 draft。"""
    pid, tid = _room_with_work(client)

    counts = _sweep(client)

    assert counts["opened"] == 1, counts
    [opened] = sweeping["opened"]
    assert opened["draft"] is True
    assert opened["head"] == _disk_branch(client, tid)


def test_a_batch_with_nothing_on_its_branch_gets_no_pr(client, sweeping):
    """一批空的活是房间两次交付之间的常态。给它开 PR = 把一个空 diff 推到人脸上。"""
    room = _make_topic(client, _make_project(client))
    delivery_task(client, room, commit=False)

    counts = _sweep(client)

    assert counts["opened"] == 0
    assert sweeping["opened"] == []


def test_a_quiet_branch_is_asked_about_without_locking_its_task(
    client, sweeping, monkeypatch
):
    """A sweep visits every open batch, and most have nothing new on them.

    Asking the forge about one of those must not hold the batch's row (and
    with it a database connection) for the length of the request: anyone
    else touching that task meanwhile would wait on GitHub."""
    from sqlalchemy import select, text

    from app.domain.room_task.models import Task

    _, room = _room_with_work(client)
    task_id = _uuid.UUID(str(delivery_task_id(client, room)))
    locked_elsewhere: list[bool] = []
    fake = sweeping["fake"]

    async def compare_status(**_kwargs):
        async with client.test_request_factory() as other:
            await other.execute(text("SET LOCAL lock_timeout = '200ms'"))
            try:
                await other.scalar(
                    select(Task.id).where(Task.id == task_id).with_for_update()
                )
                locked_elsewhere.append(False)
            except Exception:  # the lock wait is the observation
                locked_elsewhere.append(True)
            await other.rollback()
        return "identical"  # nothing new on the branch since its base

    monkeypatch.setattr(fake, "compare_status", compare_status)

    counts = _sweep(client)

    assert counts["opened"] == 0, counts
    assert locked_elsewhere == [False]


def test_github_is_asked_about_its_quota_without_the_task_locked(
    client, sweeping, monkeypatch
):
    """Before a sweep opens a draft PR it asks GitHub how much of the
    installation's hour is left. That request must not run while the task's
    row is held: the sweep's locked step relies on the answer the unlocked
    look already got, and anyone touching the task meanwhile would otherwise
    wait on GitHub."""
    from sqlalchemy import select, text

    from app.domain.room_task.models import Task

    _, room = _room_with_work(client)
    task_id = _uuid.UUID(str(delivery_task_id(client, room)))
    locked_while_asked: list[bool] = []

    async def core_quota(_self):
        async with client.test_request_factory() as other:
            await other.execute(text("SET LOCAL lock_timeout = '200ms'"))
            try:
                await other.scalar(
                    select(Task.id).where(Task.id == task_id).with_for_update()
                )
                locked_while_asked.append(False)
            except Exception:  # the lock wait is the observation
                locked_while_asked.append(True)
            await other.rollback()
        return 5000, 5000

    monkeypatch.setattr(_FakeTokens, "core_quota", core_quota)

    counts = _sweep(client)

    assert counts["opened"] == 1, counts
    assert locked_while_asked and not any(locked_while_asked), locked_while_asked


def test_a_quota_github_already_reported_spent_keeps_the_sweep_away(client, sweeping):
    """Once GitHub has put the installation under the share kept for people,
    background work leaves the rest to them: no draft PR is opened."""
    _room_with_work(client)
    _FakeTokens.quota_left = 100  # of 5000: under the share kept for people

    counts = _sweep(client)

    assert counts["opened"] == 0, counts
    assert sweeping["opened"] == []


def test_a_batch_left_open_in_an_archived_room_gets_no_pr(client, sweeping):
    """Rooms archived before archiving closed their work still hold open tasks.

    Nobody works there any more, so the sweep must not spend a GitHub request
    per tick on each of them, let alone open a PR for one.
    """
    from app.domain.topic.models import Topic, TopicStatus

    _pid, tid = _room_with_work(client)

    async def archive_without_closing() -> None:
        async with client.test_factory() as session:
            topic = await session.get(Topic, _uuid.UUID(tid))
            topic.status = TopicStatus.archived
            await session.commit()

    asyncio.run(archive_without_closing())

    counts = _sweep(client)

    assert counts == {"opened": 0, "skipped": 0, "failed": 0}
    assert sweeping["opened"] == []


def test_a_branch_quiet_for_hours_is_not_asked_about(client, sweeping):
    """草稿扫描只看最近动过的活。

    每个候选每扫一遍要问 GitHub 两次（分支头 + 比较）。一条几小时没人碰过的
    分支不会有人在往上推：等它再有人说活时再扫，而不是每一拍都花这两次。
    """
    from app.domain.room_task.models import Task

    fresh = _room_with_work(client)  # 刚刚建出来 = 10 分钟内动过
    three_hours = _room_with_work(client)
    two_days = _room_with_work(client)

    def backdate(room, ago):
        task_id = _uuid.UUID(str(delivery_task_id(client, room[1])))
        at = datetime.now(UTC) - ago

        async def _do() -> None:
            async with client.test_factory() as s:
                task = await s.get(Task, task_id)
                task.created_at = at
                await s.commit()

        asyncio.run(_do())

    backdate(three_hours, timedelta(hours=3))
    backdate(two_days, timedelta(days=2))

    counts = _sweep(client)

    assert counts["opened"] == 1, counts
    [opened] = sweeping["opened"]
    assert opened["head"] == _disk_branch(client, fresh[1])
    # 只有那一条被问过：另外两条一次 GitHub 请求都没花。一条被开出来的活
    # 会被问两次（锁外看一眼、锁内再确认），所以数的是被问过的分支头。
    assert len({head for _, head in sweeping["fake"].compare_status_calls}) == 1


def test_a_task_that_spoke_recently_is_swept_even_when_it_is_old(client, sweeping):
    """「最近动过」看的是最后一次说话，不是建这条活的时刻：一条开了两天的活，
    十分钟前还在说，就说明有人在推它的分支。"""
    from app.domain.block.models import AuthorType, Block, BlockKind
    from app.domain.room_task.models import Task

    pid, tid = _room_with_work(client)
    task_id = _uuid.UUID(str(delivery_task_id(client, tid)))

    async def seed() -> None:
        async with client.test_factory() as s:
            task = await s.get(Task, task_id)
            task.created_at = datetime.now(UTC) - timedelta(days=2)
            s.add(
                Block(
                    project_id=_uuid.UUID(pid),
                    conversation_id=task_id,
                    kind=BlockKind.message,
                    author_type=AuthorType.platform,
                    author="cheese",
                    content="还在推",
                    created_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            await s.commit()

    asyncio.run(seed())

    counts = _sweep(client)

    assert counts["opened"] == 1, counts
    assert len({head for _, head in sweeping["fake"].compare_status_calls}) == 1


def test_a_catch_up_sweep_still_asks_about_a_quiet_branch(client, sweeping):
    """掉线重连、丢了事件时的那次补扫不带窗口。

    被漏掉的那次「有提交」只能在这里补上；任务已经安静下来也一样 —— 分支上确实
    有东西。窗口是给每 300 秒那一拍省请求用的，不是「这条活不用再开 PR 了」。
    """
    from app.domain.review import pr_publish
    from app.domain.room_task.models import Task

    _, tid = _room_with_work(client)
    task_id = _uuid.UUID(str(delivery_task_id(client, tid)))

    async def backdate() -> None:
        async with client.test_factory() as s:
            task = await s.get(Task, task_id)
            task.created_at = datetime.now(UTC) - timedelta(days=2)
            await s.commit()

    asyncio.run(backdate())

    counts = client.portal.call(
        lambda: pr_publish.sweep_draft_prs(
            client.test_request_factory,
            announce=announce,
            record_instruction=record_task_instruction,
        )
    )

    assert counts["opened"] == 1, counts
    [opened] = sweeping["opened"]
    assert opened["draft"] is True


@pytest.mark.parametrize("window", [0, -3600])
def test_a_window_of_zero_or_less_sweeps_every_open_branch(
    client, sweeping, monkeypatch, window
):
    """窗口配成 0 或负数就是不设窗口 —— 顺带钉住扫的是这个设置，不是写死的数。"""
    from app.core.config import settings
    from app.domain.room_task.models import Task

    monkeypatch.setattr(settings, "draft_pr_sweep_activity_window_s", window)
    _, tid = _room_with_work(client)
    task_id = _uuid.UUID(str(delivery_task_id(client, tid)))

    async def backdate() -> None:
        async with client.test_factory() as s:
            task = await s.get(Task, task_id)
            task.created_at = datetime.now(UTC) - timedelta(days=2)
            await s.commit()

    asyncio.run(backdate())

    counts = _sweep(client)

    assert counts["opened"] == 1, counts


def test_push_fix_can_drop_dependency_before_filing_a_card(client, sweeping):
    from app.domain.room_task.models import Task, TaskStatus

    pid, tid = _room_with_work(client)
    assert _sweep(client)["opened"] == 1
    task_id = delivery_task_id(client, tid)
    number = sweeping["opened"][0]["number"]

    async def dependency():
        async with client.test_factory() as session:
            parent = Task(
                project_id=_uuid.UUID(pid),
                room_id=_uuid.UUID(tid),
                title="Rejected parent",
                branch_name="task/parent",
                base_branch="main",
                status=TaskStatus.closed,
            )
            session.add(parent)
            await session.flush()
            child = await session.get(Task, task_id)
            child.base_task_id = parent.id
            child.base_branch = parent.branch_name
            await session.commit()

    asyncio.run(dependency())
    sweeping["fake"].prs[number]["base"] = "task/parent"
    response = client.post(
        f"/topics/{task_id}/push-fix?drop_dependency=true",
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["pushed"] is True
    assert response.json()["data"]["pr_number"] == number
    assert _cards(client, tid) == []
    assert len(sweeping["opened"]) == 1
    assert sweeping["fake"].draft_by_number[number] is True
    assert sweeping["fake"].merge_calls == []
    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["base_task_id"] is None
    assert task["base_branch"] == "main"


def test_the_sweep_never_opens_a_second_pr_for_the_same_batch(client, sweeping):
    """跑几次都只有一个 PR —— 巡检每个 tick 都会重新扫到这棵树。"""
    _room_with_work(client)

    _sweep(client)
    second = _sweep(client)

    assert second["opened"] == 0
    assert len([o for o in sweeping["opened"] if not o.get("adopted")]) == 1


def test_a_batch_that_merged_after_the_list_was_taken_gets_no_pr(
    client, sweeping, monkeypatch
):
    """扫到一棵树和这棵树被交付掉，可以是同时发生的。

    名单是快照：轮到某棵树时它可能刚刚被采纳合并，而给一条已经被 squash 进 main
    的分支开 PR，开出来的是一个谁也合不掉的 PR。这里把那个窗口做成确定性的 ——
    名单已经取到，然后这一批在别的会话里合并并提交，之后巡检才轮到它。
    """
    from app.domain.review import pr_publish
    from app.domain.room_task.services import TaskService

    pid, tid = _room_with_work(client)
    task_id = delivery_task_id(client, tid)
    original = TaskService.open_without_pr

    async def _list_then_merge(self, **kwargs):
        trees = await original(self, **kwargs)

        async def _merge_it() -> None:
            async with client.test_request_factory() as s:
                svc = TaskService(s)
                landed = await svc.get(task_id)
                assert landed is not None
                landed.status = "closed"
                await s.commit()

        await _merge_it()
        return trees

    monkeypatch.setattr(TaskService, "open_without_pr", _list_then_merge)

    counts = client.portal.call(
        lambda: pr_publish.sweep_draft_prs(
            client.test_request_factory,
            announce=announce,
            record_instruction=record_task_instruction,
        )
    )

    assert counts["opened"] == 0, counts
    assert sweeping["opened"] == []


def test_one_batch_failing_does_not_cost_the_next_one_its_pr(
    client, sweeping, monkeypatch
):
    """一棵树写库失败，后面的树照样拿到 PR。

    共用一个 session 的话这条是反的：失败之后事务已经脏了，后面每一棵树都会跟着
    一起挂，一个项目的问题变成所有房间的问题。
    """
    from app.domain.room_task.services import TaskService

    first_pid, first_tid = _room_with_work(client)
    second_pid, second_tid = _room_with_work(client)
    doomed = _disk_branch(client, first_tid)
    original = TaskService.record_pr
    blown: list[str] = []

    async def _explode_once(self, tree, *, number, url):

        if git_store.branch_for_task(tree.id) == doomed and not blown:
            blown.append(doomed)
            raise RuntimeError("DB 抽风")
        return await original(self, tree, number=number, url=url)

    monkeypatch.setattr(TaskService, "record_pr", _explode_once)

    counts = _sweep(client)

    assert counts["failed"] == 1 and counts["opened"] == 1, counts
    assert _disk_branch(client, second_tid) in [o["head"] for o in sweeping["opened"]]


def test_filing_a_card_takes_the_batchs_pr_out_of_draft(client, sweeping, monkeypatch):
    """递卡的语义就是「请人来看」，所以卡一递出去，PR 就不再是 draft。

    这条要的正是 `app_world` 默认关掉的那件事（递卡时 fire-and-forget 开 PR），
    所以把它开回来，再等那份后台工作落库。
    """
    from app.domain.review import pr_publish

    monkeypatch.setattr(pr_publish, "dispatch", _REAL_DISPATCH)
    pid, tid = _room_with_work(client)
    _sweep(client)
    node_id = sweeping["prs_by_head"][_disk_branch(client, tid)]["node_id"]

    _make_card(client, tid)
    for _ in range(300):
        if sweeping["readied"]:
            break
        time.sleep(0.01)

    assert sweeping["readied"] == [node_id]
    assert sweeping["prs_by_head"][_disk_branch(client, tid)]["draft"] is False
    # 认领，不是重开：GitHub 一条 head 上只有一个开着的 PR。
    assert len([o for o in sweeping["opened"] if not o.get("adopted")]) == 1


def test_ready_flips_the_draft_and_changes_nothing_else(client, sweeping):
    """`cheese ready` 只做一件事。"""
    pid, tid = _room_with_work(client)
    _sweep(client)
    pr = sweeping["prs_by_head"][_disk_branch(client, tid)]
    assert pr["draft"] is True
    title_before, body_before = pr["title"], pr["body"]

    r = _ready(client, tid)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["ready"] is True
    assert pr["draft"] is False
    # 不合并、不改文案、不动别的字段。
    assert sweeping["fake"].merge_calls == []
    assert (pr["title"], pr["body"]) == (title_before, body_before)
    assert sweeping["patched"] == []
    assert _cards(client, tid) == []  # 也没有顺手递一张卡


def test_ready_on_a_pr_that_is_not_a_draft_says_so_instead_of_failing(client, sweeping):
    """本来就 ready 就是调用方想要的状态。为它抛异常只会教会 AI 别用这条命令。"""
    pid, tid = _room_with_work(client)
    _sweep(client)
    assert _ready(client, tid).status_code == 200

    again = _ready(client, tid)

    assert again.status_code == 200, again.text
    assert again.json()["data"]["ready"] is True
    assert again.json()["data"]["already"] is True
    assert len(sweeping["readied"]) == 1


def test_ready_reports_github_installation_rate_limit_without_changing_pr(
    client, sweeping, monkeypatch
):
    from app.domain.review.github_pr import GitHubPRClient, GitHubPRRateLimited

    _, tid = _room_with_work(client)
    _sweep(client)
    pr = sweeping["prs_by_head"][_disk_branch(client, tid)]

    async def limited(_self, _number):
        raise GitHubPRRateLimited("API rate limit exceeded for installation ID")

    monkeypatch.setattr(GitHubPRClient, "pr_view", limited)
    response = _ready(client, tid)

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["ready"] is False
    assert "额度暂时用尽" in payload["reason"]
    assert pr["draft"] is True
    assert sweeping["readied"] == []


def test_ready_never_opens_a_pr(client, sweeping):
    """`ready` 不承担「首次建 PR」。没有 PR 时它说清楚为什么，一个 PR 也不开。"""
    tid = _make_topic(client, _make_project(client))

    r = _ready(client, tid)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["ready"] is False
    assert "先提交并同步代码" in data["reason"]
    assert sweeping["opened"] == []
    assert sweeping["prs_by_head"] == {}


def test_ready_rebases_a_behind_branch_then_the_ci_reads_the_new_check_set(
    client, sweeping, monkeypatch
):
    """落后 main 的 draft：`ready` 先把 main 合进分支，再翻 ready。

    分支 CI 用的是分支 head 上的工作流。不换基的话，翻 ready 之后跑的是这条分支
    被切出来那天的工作流（旧检查集），合进 main 之后 merge_group 又要用新的跑
    一遍 —— 这就是要省掉的那一轮。换基之后，轮询读 CI 用的 head 是带着 main 上
    那套工作流的那个。"""
    pid, tid = _room_with_work(client)
    _sweep(client)
    fake = sweeping["fake"]
    pr = sweeping["prs_by_head"][_disk_branch(client, tid)]
    number = pr["number"]
    old_head = fake.prs[number]["head_sha"]
    new_head = f"{old_head}-rebased"
    # 有自己的提交、main 也往前走了 → compare 说 diverged（落后）。
    fake.compare_status_by_pair[("main", old_head)] = "diverged"
    # 旧工作流的检查集只挂在旧 head 上；main 上新工作流的检查集在换基后的 head。
    fake.check_runs_by_sha[old_head] = [
        {"name": "old-suite", "status": "completed", "conclusion": "success"}
    ]
    fake.check_runs_by_sha[new_head] = [
        {"name": "new-suite", "status": "completed", "conclusion": "success"}
    ]
    read_refs: list[str] = []
    original = fake.list_check_runs

    async def recording(*, owner, repo, ref, token):
        read_refs.append(ref)
        return await original(owner=owner, repo=repo, ref=ref, token=token)

    monkeypatch.setattr(fake, "list_check_runs", recording)

    r = _ready(client, tid)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["ready"] is True
    assert fake.update_branch_calls == [number]
    assert fake.update_branch_tokens == ["ghs_app_write"]
    assert fake.prs[number]["head_sha"] == new_head
    assert sweeping["readied"] == [pr["node_id"]]

    # 转可评审之后 CI 读的是换过基的 head —— 也就是 main 上那套工作流的检查集。
    cid = _make_card(client, tid)
    # 卡骑哪条 PR 由递卡之后那次发布写回：它认领的就是这条已经开着的 draft。
    # fixture 把 fire-and-forget 关掉了，所以这里同步跑一遍。
    _publish_the_card(client, cid, tid, pid)
    _poll(client)
    assert read_refs == [new_head]
    assert [run["name"] for run in fake.check_runs_by_sha[read_refs[-1]]] == [
        "new-suite"
    ]


# ======================= 更正卡片描述，PR 跟着改 =============================
#
# #735 是活例子：评审把 PR 正文改对了，采纳时用的却是递卡那一刻快照下来的卡片
# 描述，于是 `1c298199a` 的正文里留下一句与事实不符的历史陈述，而 main 的历史
# 不能重写。所以卡是唯一的源，改卡的同时改 PR —— 两边物理上不可能各说各话。


def _describe(client, topic_id: str, **body):
    task_id = delivery_task_id(client, topic_id)
    return client.post(
        f"/topics/{task_id}/accept-card/describe",
        headers=delivery_headers(client, topic_id),
        json=body,
    )


def test_correcting_the_card_rewrites_the_pr_and_the_commit_that_lands(
    client, sweeping
):
    fake = sweeping["fake"]
    pid, tid = _room_with_work(client)
    cid = _make_card(client, tid)
    head_sha = _give_card_a_pr(client, sweeping, tid, cid, 7)
    sweeping["prs_by_head"][_disk_branch(client, tid)] = {
        "number": 7,
        "html_url": f"https://github.com/{REPO}/pull/7",
        "title": "chore(test): file an accept card",
        "body": "递卡那一刻写的正文",
        "draft": False,
        "node_id": "PR_node_7",
    }
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    r = _describe(
        client,
        tid,
        change_subject="fix(accept): say what the review said",
        change_body="评审指出原来那句话与事实不符。",
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["change_subject"] == "fix(accept): say what the review said"
    # PR 上那份跟着改了 —— 从卡这一份长出来，不是反过来读回卡里。
    [patch] = sweeping["patched"]
    assert patch["title"] == "fix(accept): say what the review said"
    assert "评审指出原来那句话与事实不符。" in patch["body"]
    # 而且 trailer 仍然是平台写的，不是 GitHub 文本框里能改的东西。
    assert f"/topics/{tid}" in patch["body"]

    assert _accept(client, cid).status_code == 200
    [merge] = fake.merge_calls
    assert merge["commit_title"].startswith("fix(accept): say what the review said")
    assert "评审指出原来那句话与事实不符。" in merge["commit_message"]


def test_a_correction_is_refused_once_the_card_has_been_accepted(client, sweeping):
    """采纳之后 main 上那个提交已经存在，改卡改不了它 —— 那种情况的正解是在
    房间里记一条更正。"""
    fake = sweeping["fake"]
    pid, tid, cid, number, head_sha = _ready_card(client, sweeping)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")
    assert _accept(client, cid).status_code == 200

    r = _describe(client, tid, change_subject="fix(x): too late")

    assert r.status_code == 422, r.text
    assert r.json()["error"]["i18n"]["key"] == "reviewNoPendingCard"


def test_a_correction_is_validated_like_the_original_subject(client, sweeping):
    """标题走的是递卡时的同一道校验：更正入口不能成为绕过它的路。"""
    pid, tid = _room_with_work(client)
    _make_card(client, tid)

    r = _describe(client, tid, change_subject="第一行\n第二行")

    assert r.status_code == 422, r.text
    assert sweeping["patched"] == []


def test_the_review_focus_can_be_corrected_while_the_card_is_pending(client, sweeping):
    """推新提交换的是代码，卡还是同一张：递卡时写下的重点可能已经过期，而它
    和改动页一起构成「要确认什么」。它和正文一样是说明，所以能改；PR 正文里
    没有它，改它不必碰 GitHub。"""
    pid, tid = _room_with_work(client)
    _make_card(client, tid)

    r = _describe(
        client,
        tid,
        focus="原来的第一条已经不在这次改动里\n现在只需要看回执的落点",
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["focus"] == (
        "原来的第一条已经不在这次改动里\n现在只需要看回执的落点"
    )
    assert sweeping["patched"] == []
    room = _task_settled(client, tid, "现在只需要看回执的落点")
    assert "原来的第一条已经不在这次改动里" in room


def test_a_focus_correction_keeps_the_filing_limit(client, sweeping):
    """审阅重点的上限不因为换了入口就放宽：它仍是一眼要读完的三条。"""
    pid, tid = _room_with_work(client)
    _make_card(client, tid)

    r = _describe(client, tid, focus="一\n二\n三\n四")

    assert r.status_code == 422, r.text
    assert r.json()["error"]["i18n"]["key"] == "acceptFocusTooLong"


def test_a_correction_leaves_a_trace_in_the_task(client, sweeping):
    """谁在什么时候把它从什么改成了什么。这条入口能改「这次改动会在历史里说
    什么」，所以它自己必须可追溯。"""
    pid, tid = _room_with_work(client)
    _make_card(client, tid)

    r = _describe(client, tid, change_subject="fix(accept): corrected subject")
    assert r.status_code == 200, r.text

    room = _task_settled(client, tid, "fix(accept): corrected subject")
    assert "chore(test): file an accept card" in room
    assert "fix(accept): corrected subject" in room


def test_a_correction_never_touches_the_delivery_claim(client, sweeping):
    """署名是对**事实**的断言（哪件任务写的代码），描述是对改动的**说明**。
    更正入口只有后者，前者连字段都不收。"""
    pid, tid = _room_with_work(client)
    other = open_task(client, tid, "另一条活")["id"]
    _make_card(client, tid)

    sent = _describe(
        client,
        tid,
        change_subject="fix(accept): corrected subject",
        task_ids=[other],
    )

    assert sent.status_code == 200, sent.text
    assert _cards(client, tid)[0]["change_subject"] == "fix(accept): corrected subject"
    # `task_ids` 被无视，而不是被收下。
    from app.domain.review.repositories import AcceptCardRepository

    async def _claimed() -> list:
        async with client.test_factory() as s:
            card = await AcceptCardRepository(s).get(
                _uuid.UUID(_cards(client, tid)[0]["id"])
            )
            assert card is not None
            return list(card.delivered_task_ids or [])

    assert asyncio.run(_claimed()) == [str(delivery_task_id(client, tid))]


def test_a_batch_being_merged_right_now_does_not_get_a_second_pr(
    client, sweeping, monkeypatch
):
    """巡检拿到树的行锁时，这批活在 GitHub 上可能已经被合并了。

    合并先发生（merge API 返回成功），树的行才被标 merged，所以巡检在锁里读到
    的 `status` 仍然是 `open`。光看状态，它会给一条已经被 squash 进 main 的分支
    开一个谁也合并不了的 PR。

    挡住它的是「这一批已经有人递过卡了」：卡就是交付，卡一存在 PR 就归卡管，而
    没有任何一条合并路径不经过卡（正常采纳、绿了自动合并、人工放行、以及别人在
    GitHub 上直接合并之后平台补记的那条）。所以这里不用真的去合并：一张已递的卡、
    一棵仍是 `open` 的树、一条领先 main 的分支，就是巡检在那个时刻看到的全部。

    断言整次巡检一条都没失败：巡检把单条任务上的异常记成 failed 然后继续，所以
    只看 opened 为 0，巡检自己出错时也会通过。
    """
    from app.domain.review import pr_publish

    async def _looks_ahead(_factory, candidates, _blocked):
        return {task.task_id: True for task in candidates}

    # The unlocked pre-check already leaves a carded batch out. A card can
    # still be filed between that check and the lock, so this exercises the
    # locked path on its own.
    monkeypatch.setattr(pr_publish, "_github_draft_preflight", _looks_ahead)
    fake = sweeping["fake"]
    pid, tid = _room_with_work(client)
    cid = _make_card(client, tid)
    head_sha = _give_card_a_pr(client, sweeping, tid, cid, 7)
    fake.check_state_by_sha[head_sha] = ("success", "全绿")

    swept = _sweep(client)

    assert swept == {"opened": 0, "skipped": 1, "failed": 0}, swept
    assert [o for o in sweeping["opened"] if o["draft"]] == []


def test_a_card_still_waiting_to_be_settled_keeps_the_sweep_away(client, sweeping):
    """这张卡还没结算，所以巡检连问都不问 GitHub 就把这批跳过。

    判据是「有没有还没结算的卡」，不是「有没有递过卡」：一张 `pending` 的卡是
    「这批活正在交付里」，巡检在那儿开 PR 只会多出一个没人要合的；结算过的卡不
    挡，因为第二步正是靠这一点才开得出自己的 PR
    （`test_a_task_delivers_in_steps.py::test_the_next_step_gets_a_draft_pr_of_its_own`）。

    这条咬的是不加锁的预判那一步：预判先返回 False，锁里那一步这两条根本走不到
    （把它删掉这条仍然绿，`test_a_batch_being_merged_right_now_does_not_get_a_second_pr`
    管的就是那个窗口）。两条判据一起拿掉，这条和它一起红。
    """
    _pid, tid = _room_with_work(client)
    _make_card(client, tid)  # 递出去就是 pending

    counts = _sweep(client)

    assert counts == {"opened": 0, "skipped": 1, "failed": 0}, counts
    assert sweeping["opened"] == []


def _treeless_card(client, topic_id: str, status: str) -> None:
    """一张老卡：`tree_id IS NULL`（迁移 `e4c9a2f60b18` 对没有诚实值可填的卡就是
    这么留的），状态由用例指定。"""
    from app.domain.review.models import AcceptCard, AcceptStatus

    async def _do() -> None:
        async with client.test_factory() as s:
            s.add(
                AcceptCard(
                    topic_id=_uuid.UUID(topic_id),
                    focus="",
                    status=AcceptStatus(status),
                    change_subject="chore(old): a card from before trees",
                )
            )
            await s.commit()

    client.portal.call(_do)


def test_a_finished_treeless_card_does_not_switch_drafts_off_forever(client, sweeping):
    """一张历史上的无树老卡，已经采纳/作废/驳回了 —— 它不该让这个房间**以后每一
    批**都开不出 draft PR。

    它手上没有任何一个还等着被合的 PR，而拿它当「这条分支已经有 PR 了」用，等于
    一次远古交付把整个房间的 draft 永久关掉，而且没有任何地方看得出来。
    """
    pid, tid = _room_with_work(client)
    _treeless_card(client, tid, "accepted")

    counts = _sweep(client)

    assert counts["opened"] == 1, counts
    assert [o["head"] for o in sweeping["opened"] if o["draft"]] == [
        _disk_branch(client, tid)
    ]
