"""Forgejo's REST implementation of the proposal operations used by Cheese."""

from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.forge_http import forge_client
from app.core.sentences import exception_text, say
from app.domain.review import merge_check
from app.domain.review.github_pr import (
    GitHubPRError,
    GitHubPrError,
    MergeResult,
    OpenedPR,
)
from app.domain.review.github_pr_status import PullRequestStatus
from app.domain.review.pr_signals import ReviewSignal


def _has_review_comments(row: dict) -> bool:
    """这条评审是否值得再问一次它的行内评论。

    Forgejo 的评审列表里，每条评审自带 `comments_count`（按评审 id 数的代码
    评论条数），0 必然对应空列表。字段缺席说明对面不报这个数，那就照旧去取 ——
    少一次请求不值得用「静默丢意见」来换。
    """
    return row.get("comments_count") != 0


class ForgejoClient:
    def __init__(self, api_base: str, *, transport=None):
        self.api_base = api_base.rstrip("/")
        self.transport = transport

    @staticmethod
    def _repo(owner: str, repo: str) -> str:
        return f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"

    async def request(self, method, path, *, token, params=None, json=None):
        try:
            async with forge_client(transport=self.transport, timeout=30) as client:
                return await client.request(
                    method,
                    self.api_base + path,
                    headers={"Authorization": f"token {token}"},
                    params=params,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise GitHubPrError(
                say("forgejoUnreachable", error=type(exc).__name__)
            ) from exc

    @staticmethod
    def _data(response) -> Any:
        if response.is_error:
            # A provider response can repeat credentials; never echo it to a room.
            raise GitHubPrError(
                say("forgejoRequestFailed", status=response.status_code)
            )
        return response.json() if response.content else None

    async def pages(self, path, *, token, params=None):
        result = []
        page = 1
        while True:
            response = await self.request(
                "GET",
                path,
                token=token,
                params={**(params or {}), "page": page, "limit": 50},
            )
            rows = self._data(response)
            if not isinstance(rows, list):
                raise GitHubPrError(say("forgejoListUnreadable"))
            result.extend(rows)
            if not rows or ("next" not in response.links and len(rows) < 50):
                return result
            page += 1

    def git_url(self, owner: str, repo: str) -> str:
        """Where git reaches the repository: the forge at the address its API
        is called at, without `/api/v1`."""
        forge = self.api_base.removesuffix("/api/v1")
        return f"{forge}/{quote(owner, safe='')}/{quote(repo, safe='')}.git"

    async def pull_request_status(self, *, owner, repo, number, token):
        """The PR as Forgejo reports it, with Forgejo's `false` settled by git.

        `false` on an open, ready PR is a merge check still running or a
        conflict, and only the test merge tells which (`merge_check`). Its
        answer for this head and base, once there is one, decides: a conflict
        is `mergeable=False`, anything else stays unknown. Without an answer
        yet, one is started in the background and the PR reads unknown; nothing
        here waits for it."""
        data = self._data(
            await self.request(
                "GET",
                f"{self._repo(owner, repo)}/pulls/{number}",
                token=token,
            )
        )
        status = self.status(data)
        if (
            data.get("mergeable") is not False
            or status.state != "open"
            or status.merged
            or status.draft
            or not status.base_sha
        ):
            return status
        url = self.git_url(owner, repo)
        found = merge_check.answer(url, status.base_sha, status.head_sha)
        if found == "conflict":
            return replace(status, mergeable=False, mergeable_state="dirty")
        if found is None:
            merge_check.start(url, token, status.base_sha, status.head_sha)
        return status

    @staticmethod
    def status(data):
        merged = bool(data.get("merged"))
        raw_title = str(data.get("title") or "")
        title = raw_title.upper()
        draft = title.startswith(("WIP:", "[WIP]"))
        # Forgejo's `mergeable` is a plain bool, and `false` does not by
        # itself mean a conflict: `PullRequest.Mergeable` (Forgejo's
        # models/issues/pull.go) reads false while its merge check is still
        # running, which it does again after every push to either branch, as
        # well as on a conflict, a checker error and a WIP title. Nothing else
        # in the API tells these apart (the merge endpoint refuses them all
        # with the same 405). So one reading of `false` is GitHub's `null`, not
        # yet known, never the `False` the platform reads as a conflict;
        # `pull_request_status` settles it with git.
        mergeable = True if data.get("mergeable") is True else None
        return PullRequestStatus(
            head_sha=data["head"]["sha"],
            head_ref=data["head"]["ref"],
            base_ref=str((data.get("base") or {}).get("ref") or ""),
            base_sha=str((data.get("base") or {}).get("sha") or ""),
            state=data["state"],
            merged=merged,
            merge_commit_sha=data.get("merge_commit_sha") if merged else None,
            merged_at=(
                datetime.fromisoformat(data["merged_at"].replace("Z", "+00:00"))
                if merged and data.get("merged_at")
                else None
            ),
            mergeable=mergeable,
            mergeable_state="draft" if draft else "clean" if mergeable else "unknown",
            draft=draft,
            # 平台按卡片的 subject 写这个标题（`pr_publish.sync_pr_text`），仓库
            # 自己的检查评判它，平台 squash 时落的也是同一行；两条合并路径都会
            # 把它和卡片比一遍（`pr_text.pr_title_matches_subject`），不一致就停。
            # 不带上它，每一张 Forgejo 的卡都会被读成「标题读不出来」而永远合不了。
            title=raw_title,
            review_comment_count=data.get("review_comments", 0),
        )

    async def pull_request_head_sha(self, **kwargs):
        return (await self.pull_request_status(**kwargs)).head_sha

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
    ):
        if not sha:
            raise GitHubPrError(say("acceptNeedsViewedCommit"))
        response = await self.request(
            "POST",
            f"{self._repo(owner, repo)}/pulls/{number}/merge",
            token=token,
            json={
                "Do": settings.accept_pr_merge_method,
                "head_commit_id": sha,
                "MergeTitleField": commit_title or "",
                "MergeMessageField": commit_message or "",
                "delete_branch_after_merge": False,
                "force_merge": False,
            },
        )
        if response.status_code in (405, 409, 422):
            live = await self.pull_request_status(
                owner=owner,
                repo=repo,
                number=number,
                token=token,
            )
            if live.merged:
                return MergeResult(sha=live.merge_commit_sha)
            if live.head_sha != sha:
                return MergeResult(
                    stale_head=True, blocked_reason="内容已有更新，请重新查看"
                )
            reason = (
                "草稿还未提交评审"
                if live.draft
                else "改动与目标版本冲突"
                if live.mergeable is False
                else "代码托管服务尚未算出 PR 能否合并"
                if live.mergeable is None
                else "检查或评审条件尚未满足"
            )
            return MergeResult(blocked_reason=reason)
        self._data(response)
        live = await self.pull_request_status(
            owner=owner, repo=repo, number=number, token=token
        )
        if not live.merged or not live.merge_commit_sha:
            raise GitHubPrError(say("forgejoMergeUnconfirmed"))
        return MergeResult(sha=live.merge_commit_sha)

    async def list_check_runs(self, *, owner, repo, ref, token):
        rows = await self.pages(
            f"{self._repo(owner, repo)}/commits/{quote(ref, safe='')}/statuses",
            token=token,
        )
        latest = {}
        for row in sorted(rows, key=lambda r: r["id"], reverse=True):
            latest.setdefault(row["context"], row)
        return [
            {
                "name": name,
                "status": "in_progress" if row["status"] == "pending" else "completed",
                "conclusion": {
                    "success": "success",
                    "failure": "failure",
                    "error": "failure",
                }.get(row["status"]),
                "html_url": row.get("target_url"),
                "details": row.get("description", ""),
            }
            for name, row in latest.items()
        ]

    async def check_state(self, **kwargs):
        runs = await self.list_check_runs(**kwargs)
        if not runs:
            return "no_checks", "没有检查报告"
        failed = [r for r in runs if r["conclusion"] == "failure"]
        if failed:
            return "failure", "\n".join(f"{r['name']}：{r['details']}" for r in failed)
        if any(r["status"] != "completed" for r in runs):
            return "pending", "检查仍在运行"
        return "success", "检查已通过"

    async def compare_files(self, *, owner, repo, base, head, token):
        comparison = f"{quote(base, safe='')}...{quote(head, safe='')}"
        data = self._data(
            await self.request(
                "GET",
                f"{self._repo(owner, repo)}/compare/{comparison}",
                token=token,
            )
        )
        files = data.get("files")
        if files is None:
            return None
        return [(r.get("status", "modified"), r["filename"]) for r in files]

    async def compare_status(self, *, owner, repo, base, head, token):
        counts = []
        for left, right in ((base, head), (head, base)):
            comparison = f"{quote(left, safe='')}...{quote(right, safe='')}"
            data = self._data(
                await self.request(
                    "GET",
                    f"{self._repo(owner, repo)}/compare/{comparison}",
                    token=token,
                )
            )
            counts.append(data["total_commits"])
        ahead, behind = counts
        return (
            "diverged"
            if ahead and behind
            else "ahead"
            if ahead
            else "behind"
            if behind
            else "identical"
        )

    async def review_signals(self, *, owner, repo, number, token, with_comments=True):
        prefix = f"{self._repo(owner, repo)}/pulls/{number}"
        reviews = await self.pages(prefix + "/reviews", token=token)
        result = []
        for row in reviews:
            kind = {"REQUEST_CHANGES": "changes_requested", "COMMENT": "commented"}.get(
                row["state"]
            )
            if kind and row.get("body") and not row.get("dismissed"):
                result.append(
                    ReviewSignal(
                        id=f"review:{row['id']}",
                        kind=kind,
                        author=row["user"]["login"],
                        body=row["body"],
                    )
                )
            # 上面那个判断说的是这条评审自己的信号，与它的行内评论无关：空正文
            # 的评审行内评论照旧要取。这里省掉的是另一件事 —— 列表已经报了这条
            # 评审没有行内评论，再问一次只会拿回空列表。
            if with_comments and _has_review_comments(row):
                comments = await self.pages(
                    prefix + f"/reviews/{row['id']}/comments", token=token
                )
                for comment in comments:
                    result.append(
                        ReviewSignal(
                            id=f"comment:{comment['id']}",
                            kind="comment",
                            author=comment["user"]["login"],
                            body=comment["body"],
                            where=comment.get("path", ""),
                        )
                    )
        comments = await self.pages(
            f"{self._repo(owner, repo)}/issues/{number}/comments", token=token
        )
        result.extend(
            ReviewSignal(
                id=f"issue-comment:{r['id']}",
                kind="commented",
                author=r["user"]["login"],
                body=r["body"],
            )
            for r in comments
            if r.get("body")
        )
        return result

    async def update_branch(
        self, *, owner, repo, number, token, expected_head_sha=None
    ):
        if expected_head_sha:
            live = await self.pull_request_head_sha(
                owner=owner, repo=repo, number=number, token=token
            )
            if live != expected_head_sha:
                return False
        response = await self.request(
            "POST",
            f"{self._repo(owner, repo)}/pulls/{number}/update",
            token=token,
            params={"style": "merge"},
        )
        if response.status_code in (409, 422):
            return False
        self._data(response)
        return True


class ForgejoPRClient:
    """Repository-bound publisher; uses the same REST client as acceptance."""

    def __init__(self, owner, repo, tokens, *, api_base, transport=None):
        self.owner, self.repo, self.tokens = owner, repo, tokens
        self.client = ForgejoClient(api_base, transport=transport)
        self.path = self.client._repo(owner, repo)

    async def _request(self, method, path, **kwargs):
        token, _ = await self.tokens.write_token()
        try:
            response = await self.client.request(
                method, self.path + path, token=token, **kwargs
            )
            return self.client._data(response)
        except GitHubPrError as exc:
            raise GitHubPRError(exception_text(exc)) from exc

    @staticmethod
    def _proposal(data):
        return {
            **data,
            "node_id": str(data["number"]),
            "draft": data["title"].upper().startswith(("WIP:", "[WIP]")),
        }

    async def open_pr(
        self, *, head, base, title, body, draft=False, as_user_token=None
    ) -> OpenedPR:
        # A Forgejo project has no account of the owner's to open it with.
        del as_user_token
        token, _ = await self.tokens.write_token()
        # Matching both refs prevents adopting another task's proposal.
        existing = await self.client.pages(
            self.path + "/pulls", token=token, params={"state": "open"}
        )
        for pr in existing:
            if pr["head"]["ref"] == head and pr["base"]["ref"] == base:
                return OpenedPR(self._proposal(pr))
        pr = self._proposal(
            await self._request(
                "POST",
                "/pulls",
                json={
                    "head": head,
                    "base": base,
                    "title": (
                        f"WIP: {title}"
                        if draft and not title.upper().startswith(("WIP:", "[WIP]"))
                        else title
                    ),
                    "body": body,
                },
            )
        )
        return OpenedPR(pr)

    async def pr_view(self, number):
        return self._proposal(await self._request("GET", f"/pulls/{number}"))

    async def pr_status(self, number):
        return self.client.status(await self.pr_view(number))

    async def update_pr(self, number, *, title=None, body=None, base=None):
        payload = {
            k: v
            for k, v in {"title": title, "body": body, "base": base}.items()
            if v is not None
        }
        return self._proposal(
            await self._request("PATCH", f"/pulls/{number}", json=payload)
        )

    async def _commit(self, sha):
        """Commit `sha` as Forgejo answers it — the one read `commit_subject`
        and `commit_parents` project from.

        `/git/commits/{sha}`, not `/commits/{sha}`: Forgejo routes the single
        commit under `/git` (Gitea's GitHub-compatible alias for it is not in
        Forgejo), which is the path `forge_files.py` already reads a commit
        at."""
        return await self._request("GET", f"/git/commits/{quote(sha, safe='')}")

    async def commit_subject(self, sha):
        """The first line of commit `sha`'s message, or None when Forgejo
        answers without one — `GitHubPRClient.commit_subject` on this forge.

        Forgejo has no draft flag: a proposal is a draft exactly when its
        title starts with `WIP:`, so `mark_ready_for_review` below "un-drafts"
        it by rewriting that title. What it rewrites it TO is the placeholder
        minus the prefix — the task's own (Chinese) title — which is why the
        reader of this method puts a real title on first."""
        data = await self._commit(sha)
        # `_data` hands back None for a 200 with an empty body: no message is
        # the same answer as an empty one, and neither is a crash.
        message = (((data or {}).get("commit") or {}).get("message") or "").strip()
        return message.splitlines()[0] if message else None

    async def commit_parents(self, sha):
        """The shas of commit `sha`'s parents, first parent first; empty for
        a root commit — `GitHubPRClient.commit_parents` on this forge.

        Read by `AcceptService.mark_ready` before `commit_subject`: a line
        that resolved its lag with `git merge main` ends on a two-parent merge
        commit whose subject is 「merge main into …」, not the title the work
        lands under. The caller follows the first parent until a single-parent
        commit turns up."""
        data = await self._commit(sha)
        return [
            str(parent.get("sha"))
            for parent in ((data or {}).get("parents") or [])
            if parent.get("sha")
        ]

    async def mark_ready_for_review(self, node_id):
        pr = await self.pr_view(int(node_id))
        title = pr["title"]
        if title.upper().startswith("WIP:"):
            title = title[4:].lstrip()
        elif title.upper().startswith("[WIP]"):
            title = title[5:].lstrip()
        await self.update_pr(int(node_id), title=title)

    async def close_pr(self, number):
        return await self._request(
            "PATCH", f"/pulls/{number}", json={"state": "closed"}
        )

    async def comment(self, number, body):
        return await self._request(
            "POST", f"/issues/{number}/comments", json={"body": body}
        )

    async def post_review(self, number, *, commit_id, body, comments):
        """One review of line comments; when Forgejo refuses it, one PR comment
        carrying them all, so the record still reaches the PR."""
        payload = {
            "event": "COMMENT",
            "body": body,
            "comments": [
                {"path": c["path"], "new_position": c["line"], "body": c["body"]}
                for c in comments
            ],
        }
        if commit_id:
            payload["commit_id"] = commit_id
        try:
            return await self._request("POST", f"/pulls/{number}/reviews", json=payload)
        except GitHubPRError:
            folded = "\n\n".join(
                [body, *(f"`{c['path']}:{c['line']}`\n{c['body']}" for c in comments)]
            )
            return await self.comment(number, folded)

    async def check_runs(self, ref):
        token, _ = await self.tokens.installation_token()
        runs = await self.client.list_check_runs(
            owner=self.owner, repo=self.repo, ref=ref, token=token
        )
        return [
            {
                "name": run["name"],
                "status": run["status"],
                "conclusion": run["conclusion"],
                "url": run.get("html_url"),
            }
            for run in runs
        ]
