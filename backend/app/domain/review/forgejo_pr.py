"""Forgejo's REST implementation of the proposal operations used by Cheese."""

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.domain.review.github_pr import (
    GitHubPRError,
    GitHubPrError,
    MergeResult,
    PullRequestStatus,
)
from app.domain.review.pr_signals import ReviewSignal


class ForgejoClient:
    def __init__(self, api_base: str, *, transport=None):
        self.api_base = api_base.rstrip("/")
        self.transport = transport

    @staticmethod
    def _repo(owner: str, repo: str) -> str:
        return f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"

    async def request(self, method, path, *, token, params=None, json=None):
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=30
            ) as client:
                return await client.request(
                    method,
                    self.api_base + path,
                    headers={"Authorization": f"token {token}"},
                    params=params,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise GitHubPrError(f"Forgejo 暂时无法连接：{type(exc).__name__}") from exc

    @staticmethod
    def _data(response) -> Any:
        if response.is_error:
            # A provider response can repeat credentials; never echo it to a room.
            raise GitHubPrError(f"Forgejo 请求失败（HTTP {response.status_code}）")
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
                raise GitHubPrError("Forgejo 返回了无法读取的列表")
            result.extend(rows)
            if not rows or ("next" not in response.links and len(rows) < 50):
                return result
            page += 1

    async def pull_request_status(self, *, owner, repo, number, token):
        data = self._data(
            await self.request(
                "GET",
                f"{self._repo(owner, repo)}/pulls/{number}",
                token=token,
            )
        )
        return self.status(data)

    @staticmethod
    def status(data):
        merged = bool(data.get("merged"))
        title = data.get("title", "").upper()
        draft = title.startswith(("WIP:", "[WIP]"))
        mergeable = data.get("mergeable")
        return PullRequestStatus(
            head_sha=data["head"]["sha"],
            head_ref=data["head"]["ref"],
            state=data["state"],
            merged=merged,
            merge_commit_sha=data.get("merge_commit_sha") if merged else None,
            merged_at=(
                datetime.fromisoformat(data["merged_at"].replace("Z", "+00:00"))
                if merged and data.get("merged_at")
                else None
            ),
            mergeable=mergeable,
            mergeable_state="draft"
            if draft
            else "dirty"
            if mergeable is False
            else "clean"
            if mergeable is True
            else "unknown",
            draft=draft,
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
            raise GitHubPrError("采纳需要已查看版本的提交编号")
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
                else "检查或评审条件尚未满足"
            )
            return MergeResult(blocked_reason=reason)
        self._data(response)
        live = await self.pull_request_status(
            owner=owner, repo=repo, number=number, token=token
        )
        if not live.merged or not live.merge_commit_sha:
            raise GitHubPrError("Forgejo 尚未确认合并结果，请刷新后重试")
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

    async def check_run_names(self, **kwargs):
        return {r["name"] for r in await self.list_check_runs(**kwargs)}

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
            if with_comments:
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
            raise GitHubPRError(str(exc)) from exc

    @staticmethod
    def _proposal(data):
        return {
            **data,
            "node_id": str(data["number"]),
            "draft": data["title"].upper().startswith(("WIP:", "[WIP]")),
        }

    async def open_pr(
        self, *, head, base, title, body, draft=False, as_user_token=None
    ):
        token, _ = await self.tokens.write_token()
        # Matching both refs prevents adopting another task's proposal.
        existing = await self.client.pages(
            self.path + "/pulls", token=token, params={"state": "open"}
        )
        for pr in existing:
            if pr["head"]["ref"] == head and pr["base"]["ref"] == base:
                return self._proposal(pr)
        return self._proposal(
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
