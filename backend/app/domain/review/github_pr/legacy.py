"""`GitHubPRClient` — #188 §5.1's original client, and still the one in use.

Bound to one repo and a `GitHubAppTokens` (write mint for push/open, read-only
mint for check runs), so a call carries no token of its own. `pr_publish` opens
the card's PR with it fire-and-forget when the card is filed, and the accept
hand-over (`cheese ready`, correcting a description, merging, closing) drives
the same shape.

Its errors are its own — `GitHubPRError` and its two subclasses — because every
caller on this lane already handles those. `GitHubPrError` in `_shared` belongs
to the other shape, and the two are unrelated siblings.
"""

import functools
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.forge_http import forge_client
from app.core.sentences import exception_text, say
from app.domain.project.github_app import GitHubAppTokens
from app.domain.review.github_pr._shared import (
    GitHubPrError,
    _enqueue_if_required,
)
from app.domain.review.github_pr_status import (
    PullRequestStatus,
    parse_pull_request_status,
)
from app.domain.review.superseded_runs import drop_superseded

logger = logging.getLogger("cheesex.review.github_pr")


class GitHubPRError(RuntimeError):
    """GitHub refused an operation (or the network did)."""


class GitHubPRRateLimited(GitHubPRError):
    """The GitHub App installation has temporarily exhausted its API quota."""


class GitHubPRMergeBlocked(GitHubPRError):
    """The merge was refused because the PR is not mergeable (conflict)."""


@dataclass(frozen=True, slots=True)
class OpenedPR:
    """The PR opened or adopted for a task."""

    pr: dict


def _as_pr_error[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """Translate a failure to *reach* GitHub into this module's own error.

    Every caller already handles `GitHubPRError`; none of them handled a raw
    `httpx.ConnectError`, so a DNS blip or a TLS handshake that never finished
    came out of `pr_view` unchanged and became a 500 with a traceback in the room
    (`/pr-checks` polls every few seconds, so it was a 500 every few seconds).
    Unreachable is not a different KIND of failure from "GitHub said no" for
    anyone upstream — it is the same "I could not learn the PR's state", and
    that is what the error class already claims to mean.

    Wrapping the whole method, not just the request, is deliberate: minting the
    installation token (`GitHubAppTokens`) talks to GitHub too, so the network
    can drop on that line just as easily as on the call after it.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except httpx.RequestError as exc:
            raise GitHubPRError(
                f"GitHub unreachable ({type(exc).__name__}: {exc})"[:300]
            ) from exc

    return wrapper


class GitHubPRClient:
    def __init__(
        self,
        owner: str,
        repo: str,
        tokens: GitHubAppTokens,
        *,
        api_base: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._owner = owner
        self._repo = repo
        self._tokens = tokens
        self._api_base = api_base.rstrip("/")
        self._transport = transport

    @property
    def tokens(self) -> GitHubAppTokens:
        return self._tokens

    def _url(self, path: str) -> str:
        return f"{self._api_base}/repos/{self._owner}/{self._repo}{path}"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    @_as_pr_error
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
        """Open (or find the already-open) PR for a branch.

        Re-submitting a card for the same topic must not fail on GitHub's
        "a pull request already exists" — the existing PR IS this topic's PR.

        `draft` opens it as a draft — GitHub's word for 进行中 (#718 拍板①).
        REST can only open one that way; it cannot flip an existing PR either
        direction, which is why `mark_ready_for_review` below has to speak
        GraphQL. An adopted PR keeps whatever draft state it already had: this
        argument describes the PR being CREATED, and re-deriving the state of
        one that already exists is `mark_ready_for_review`'s job.

        `as_user_token` is the task owner's own user-to-server token. GitHub
        credits a PR to whoever's credential opened it, and its squash commit
        too, so this decides whose name the change lands under; an App cannot
        open a PR as anybody else. When their token cannot open it (no write
        access, authorization revoked) the App opens it, and the trailers credit
        the owner as a co-author instead (`credit.attribution`).
        """
        app_token, _ = await self._tokens.write_token()
        payload: dict[str, object] = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
        }
        if draft:
            payload["draft"] = True
        async with forge_client(transport=self._transport, timeout=30.0) as client:

            async def create(token: str) -> httpx.Response:
                return await client.post(
                    self._url("/pulls"), json=payload, headers=self._headers(token)
                )

            resp = await create(as_user_token) if as_user_token else None
            if resp is not None and resp.status_code != 201:
                if resp.status_code != 422 or "already exist" not in resp.text:
                    logger.info(
                        "the task owner's token could not open a PR on %s "
                        "(HTTP %s); the App opens it",
                        head,
                        resp.status_code,
                    )
                    resp = None
            if resp is None:
                resp = await create(app_token)
            if resp.status_code == 201:
                return OpenedPR(resp.json())
            if resp.status_code == 422 and "already exist" in resp.text:
                listing = await client.get(
                    self._url("/pulls"),
                    params={"head": f"{self._owner}:{head}", "state": "open"},
                    headers=self._headers(app_token),
                )
                if listing.status_code == 200 and listing.json():
                    # An adopted PR was opened earlier under whatever identity
                    # opened it then; this call substituted nothing.
                    return OpenedPR(listing.json()[0])
            raise GitHubPRError(
                f"PR creation failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

    @_as_pr_error
    async def pr_view(self, number: int) -> dict:
        token, _ = await self._tokens.write_token()
        async with forge_client(transport=self._transport, timeout=20.0) as client:
            resp = await client.get(
                self._url(f"/pulls/{number}"), headers=self._headers(token)
            )
        if resp.status_code != 200:
            # 403 is the quota and a missing permission alike; only the phrase
            # tells them apart, and only the quota is worth retrying.
            if resp.status_code == 403 and "API rate limit exceeded" in resp.text:
                raise GitHubPRRateLimited(
                    f"PR read rate limited (HTTP 403): {resp.text[:300]}"
                )
            raise GitHubPRError(
                f"PR read failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return resp.json()

    @_as_pr_error
    async def commit_subject(self, sha: str) -> str | None:
        """The first line of commit `sha`'s message, or None when GitHub
        answers without one.

        Read by `AcceptService.mark_ready`: a draft PR still titled with the
        platform's placeholder is marked ready under the commit's own title,
        because the full `CI required` that the ready flag starts reads the
        PR's title, and a repository checking its own titles reads nothing
        but `WIP:` there.

        A refusal raises where the rest of this class raises — `GitHubPRError`,
        or `GitHubPRRateLimited` for the quota — not `GitHubPrError`: the two
        are unrelated siblings, and only the first pair is what callers along
        the ready path handle (`_as_pr_error`'s docstring). `_refused` above is
        the older client's idiom and belongs to that one."""
        token, _ = await self._tokens.write_token()
        async with forge_client(transport=self._transport, timeout=20.0) as client:
            resp = await client.get(
                self._url(f"/commits/{sha}"), headers=self._headers(token)
            )
        if resp.status_code != 200:
            refusal = say(
                "githubRefusedCommitRead",
                status=resp.status_code,
                reply=resp.text[:300],
            )
            if resp.status_code == 403 and "API rate limit exceeded" in resp.text:
                raise GitHubPRRateLimited(refusal)
            raise GitHubPRError(refusal)
        message = ((resp.json().get("commit") or {}).get("message") or "").strip()
        return message.splitlines()[0] if message else None

    async def pr_status(self, number: int) -> PullRequestStatus:
        """`pr_view`, parsed — the structured twin of the raw dict.

        Exists (#718) so this lane's callers get `mergeable_state`, the
        three-valued `mergeable` and the merged-gated `merge_commit_sha`
        through the SAME parser as the poller lane
        (`parse_pull_request_status`), instead of each caller sniffing the
        raw json ad hoc."""
        return parse_pull_request_status(await self.pr_view(number))

    @_as_pr_error
    async def update_pr(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        base: str | None = None,
    ) -> dict:
        """Rewrite the PR's title and description — nothing else.

        The card is the single source of both, so correcting the card
        (`AcceptService.redescribe`) writes here in the same breath: what a
        reviewer reads on GitHub and what lands in `git log` come out of one
        value, and cannot drift into saying two different things about one
        change (#735 landed a description the review had already corrected).
        """
        token, _ = await self._tokens.write_token()
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.patch(
                self._url(f"/pulls/{number}"),
                json={
                    key: value
                    for key, value in {
                        "title": title,
                        "body": body,
                        "base": base,
                    }.items()
                    if value is not None
                },
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            refusal = say(
                "githubPrBodyUpdateFailed",
                status=resp.status_code,
                reply=resp.text[:300],
            )
            # Same quota, second call of the ready hand-over (`pr_view`).
            if resp.status_code == 403 and "API rate limit exceeded" in resp.text:
                raise GitHubPRRateLimited(refusal)
            raise GitHubPRError(refusal)
        return resp.json()

    @_as_pr_error
    async def close_pr(self, number: int) -> dict:
        """Close the PR without merging it. Its branch is left where it is."""
        token, _ = await self._tokens.write_token()
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.patch(
                self._url(f"/pulls/{number}"),
                json={"state": "closed"},
                headers=self._headers(token),
            )
        status, reply = resp.status_code, resp.text[:300]
        if status != 200:
            raise GitHubPRError(say("githubPrCloseFailed", status=status, reply=reply))
        return resp.json()

    @_as_pr_error
    async def mark_ready_for_review(self, node_id: str) -> None:
        """Flip a draft PR to ready using GitHub's GraphQL API.

        REST can open a PR as a draft and cannot take it out of draft:
        `PATCH /pulls/{n}` has no `draft` field, and GitHub exposes the
        transition only as the GraphQL mutation `markPullRequestReadyForReview`,
        keyed by the PR's node id (which the REST response already carries, so
        nothing has to be stored for this).

        Already-ready is not an error and not this method's business to detect:
        the mutation is idempotent, and the caller (`AcceptService.mark_ready`)
        is the one that knows whether it had anything to flip.

        Failures raise. A draft PR that silently stayed draft is a delivery
        nobody can review while everything on the platform says it is waiting
        for them.
        """
        token, _ = await self._tokens.write_token()
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.post(
                f"{self._api_base}/graphql",
                json={
                    "query": (
                        "mutation($id:ID!){markPullRequestReadyForReview("
                        "input:{pullRequestId:$id}){pullRequest{isDraft}}}"
                    ),
                    "variables": {"id": node_id},
                },
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            refusal = say(
                "githubPrReadyFailed",
                status=resp.status_code,
                reply=resp.text[:300],
            )
            # Same quota, last call of the hand-over (`update_pr`).
            if resp.status_code == 403 and "API rate limit exceeded" in resp.text:
                raise GitHubPRRateLimited(refusal)
            raise GitHubPRError(refusal)
        # GraphQL answers 200 with an `errors` array, so a non-200 check alone
        # would read every refusal as a success.
        payload = resp.json()
        if payload.get("errors"):
            raise GitHubPRError(
                say("githubPrReadyFailedErrors", errors=str(payload["errors"])[:300])
            )

    @_as_pr_error
    async def merge_pr(self, number: int, *, title: str, message: str) -> dict:
        """Merge the PR using `settings.accept_pr_merge_method` — same reason
        as the 两阶段采纳 client above: this used to hardcode a merge commit,
        which a squash-only repo (ours) refuses with 405 forever.

        405 (not mergeable) raises GitHubPRMergeBlocked — the caller routes it
        to the existing conflict-resolution flow. Anything else is a plain
        GitHubPRError.
        """
        token, _ = await self._tokens.write_token()
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            try:
                queued = await _enqueue_if_required(
                    client,
                    self._api_base,
                    self._headers(token),
                    self._owner,
                    self._repo,
                    number,
                    None,
                )
            except GitHubPrError as exc:
                raise GitHubPRError(exception_text(exc)) from exc
            if queued is not None:
                return {"merged": False, "queued": True, "sha": None}
            resp = await client.put(
                self._url(f"/pulls/{number}/merge"),
                json={
                    "merge_method": settings.accept_pr_merge_method,
                    "commit_title": title,
                    "commit_message": message,
                },
                headers=self._headers(token),
            )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 405:
            raise GitHubPRMergeBlocked(
                f"PR #{number} is not mergeable: {resp.text[:300]}"
            )
        raise GitHubPRError(
            f"PR merge failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    @_as_pr_error
    async def check_runs(self, ref: str) -> list[dict]:
        """Simplified check runs for a ref (branch name or sha) — display only.

        Uses the full installation mint, which carries `checks: read`; the
        named write set does not (`github_app._WRITE_PERMISSIONS`).
        """
        token, _ = await self._tokens.installation_token()
        async with forge_client(transport=self._transport, timeout=20.0) as client:
            resp = await client.get(
                self._url(f"/commits/{ref}/check-runs"),
                params={"per_page": 50},
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            raise GitHubPRError(
                f"check-runs read failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        # 卡面数「未通过」用的就是这一份：被顶替的旧运行留下的 check run 一个都
        # 不该出现在这里。
        kept = await drop_superseded(
            resp.json().get("check_runs", []),
            self._api_base,
            self._owner,
            self._repo,
            ref,
            self._headers(token),
            self._transport,
        )
        return [
            {
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "url": run.get("html_url"),
            }
            for run in kept
        ]
