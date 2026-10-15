"""Short-lived GitHub tokens for sandboxes (#188 minimal item 1, #192 install flow).

The platform's GitHub credential is the cheesex-app private key. It stays on
the backend and is NEVER handed to a sandbox. An agent that works on a repo
calls ``/sandbox/forge-token`` with its scoped cheese token; the backend mints
an installation access token with the App grants on the bound repository.
GitHub expires it after an hour;
minting is cached until shortly before expiry, so a burst of calls costs one
upstream mint.

Same containment shape as the LLM gateway path (``llm_proxy``): what the
sandbox holds is short-lived, scoped to the bound repository, and centrally
revocable, while the long-lived secret behind it never moves. That is the
containment. Permission levels remain those granted to the App.
An agent is a full member of the room
(``docs/agent-principles.md`` §2), so it commits, pushes and opens its PR with
the same grants the platform itself would have used on its behalf.

Which *installation* to mint from is resolved per-project (#192): the App
id and private key are one platform-wide credential, but each connected repo
has its own installation_id, looked up from ``project_git_installations``.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import forge_quota
from app.core.config import settings
from app.core.forge_http import forge_client
from app.domain.project.models import ProjectGitInstallation
from app.domain.project.repositories import ProjectGitInstallationRepository

# What PR-based accept needs (#188 §5.1): push the topic branch, open and merge
# the PR. Named rather than taken from the grant map so that a revoked grant is
# refused at the mint, where the message says which one.
# `workflows: write` is load-bearing, not optional: without it GitHub rejects
# the PUSH of any branch that touches .github/workflows/* ("refusing to allow a
# GitHub App to create or update workflow ... without `workflows` permission"),
# which surfaced as an opaque 422 on accept (2026-08-16, topic ee17b136). The
# App has held this grant all along — the mint request simply never asked, and
# GitHub narrows to what is asked.
_WRITE_PERMISSIONS = {
    "contents": "write",
    "metadata": "read",
    "pull_requests": "write",
    "workflows": "write",
}
# The grants GitHub offers at the write level only. A read-only mint asking
# for one of them at `read` is refused whole (`read_permissions`).
_WRITE_ONLY_PERMISSIONS = frozenset({"workflows"})
# GitHub caps app JWTs at 10 minutes; stay clear of clock-skew rejections.
_JWT_TTL_S = 540
# Re-mint when the cached token has less life left than a long agent turn.
_REFRESH_MARGIN_S = 20 * 60
# How long a fetched grant map is trusted. Grants only change when a human
# edits the App, so this is really "how fast that edit reaches sandboxes".
_GRANTS_TTL_S = 10 * 60


logger = logging.getLogger(__name__)

#: The status of the last `/rate_limit` answer that carried no quota, per
#: installation, so a lasting refusal is logged when it starts, not per check.
_unreadable_quota: dict[int, int] = {}


class GitHubAppError(RuntimeError):
    """Minting failed — config missing or GitHub refused."""


class GitHubAppTokens:
    """Mints (and caches) installation tokens for one installation."""

    def __init__(
        self,
        *,
        app_id: int,
        private_key_path: str,
        installation_id: int,
        repository: str | None = None,
        repository_id: int | None = None,
        api_base: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._app_id = app_id
        self._key_path = private_key_path
        self._installation_id = installation_id
        self._repository = repository
        self._repository_id = repository_id
        self._api_base = api_base.rstrip("/")
        self._transport = transport
        self._key_text: str | None = None
        # One cache slot per permission set: {slot: (token, expires_epoch)}.
        self._cached: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        # What GitHub says this installation was granted, and when we asked.
        self._grants: dict[str, str] | None = None
        self._grants_at = 0.0
        self._grants_lock = asyncio.Lock()

    @property
    def api_base(self) -> str:
        return self._api_base

    @property
    def transport(self) -> httpx.AsyncBaseTransport | None:
        return self._transport

    @property
    def installation_id(self) -> int:
        return self._installation_id

    def _key(self) -> str:
        if self._key_text is None:
            self._key_text = Path(self._key_path).read_text()
        return self._key_text

    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + _JWT_TTL_S, "iss": str(self._app_id)},
            self._key(),
            algorithm="RS256",
        )

    async def installation_token(self) -> tuple[str, str]:
        """An installation token and its ISO expiry, carrying every grant.

        This is what ``/sandbox/forge-token`` hands an agent, so it has to be
        enough to finish a piece of work: commit, push the branch, open the PR,
        then read the CI it triggered. Nothing is subtracted on the way out —
        see the module docstring.
        """
        return await self._mint("installation", self.granted_permissions)

    async def read_token(self) -> tuple[str, str]:
        """An installation token that reads what the installation may read and
        writes nothing: what ``/sandbox/forge-token`` hands a session whose
        work is not kept (a 支线, a task not yet started). GitHub enforces it,
        so a push or a new pull request from such a session is refused there."""
        return await self._mint("read", self.read_permissions)

    async def read_permissions(self) -> dict[str, str]:
        """Every grant of the installation at its read level. A grant GitHub
        has no read level for (`workflows`) is left out: asking for it would
        fail the whole mint."""
        return {
            key: "read"
            for key in await self.granted_permissions()
            if key not in _WRITE_ONLY_PERMISSIONS
        }

    async def write_token(self) -> tuple[str, str]:
        """The named write set, used by PR-based accept.

        Not a smaller share of the installation token above — it is the same
        App, and an agent's token carries at least as much. It is a separate
        mint only so that the permissions accept depends on are stated out loud
        and a revoked one fails at the mint (`_write_permissions`).
        """
        return await self._mint("write", self._write_permissions)

    async def _write_permissions(self) -> dict[str, str]:
        """The backend-internal write set, named rather than asked for whole.

        Spelled out because a revoked grant has to fail HERE: if an admin took
        `contents: write` away, GitHub says "not granted" at the mint, where the
        message is readable, instead of handing back a weaker token that dies
        later at `git push` with an unexplained 403.
        """
        return _WRITE_PERMISSIONS

    async def granted_permissions(self) -> dict[str, str]:
        """Permissions this installation holds, cached for `_GRANTS_TTL_S`.

        Doubles as the sandbox mint's request: asking for exactly the grant map
        is the one request GitHub can never 422 (it rejects the WHOLE mint when
        any requested permission was never granted), and it means the day an
        org admin adds a permission to cheesex-app, agents pick it up on the
        next mint — no deploy, no code change. The sandbox-facing route reports
        it verbatim, so an agent learns what it may do from the payload instead
        of from a 403 halfway through a turn.

        Guarded by its own lock, never `self._lock` (which `_mint` holds while
        calling this) — two locks, one order, no deadlock.
        """
        async with self._grants_lock:
            fresh = time.time() - self._grants_at < _GRANTS_TTL_S
            if self._grants is not None and fresh:
                return self._grants
            try:
                grants = await self._fetch_granted_permissions()
            except GitHubAppError:
                # A transient blip must not silently shrink a sandbox's token:
                # keep serving the last known map. With nothing cached there is
                # no honest answer, so the error travels.
                if self._grants is None:
                    raise
                return self._grants
            self._grants = grants
            self._grants_at = time.time()
            return grants

    async def _fetch_granted_permissions(self) -> dict[str, str]:
        """Ask GitHub what this installation was granted, via the App JWT."""
        async with forge_client(transport=self._transport, timeout=20.0) as client:
            resp = await client.get(
                f"{self._api_base}/app/installations/{self._installation_id}",
                headers={
                    "Authorization": f"Bearer {self._app_jwt()}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code != 200:
            raise GitHubAppError(
                f"GitHub refused to describe the installation "
                f"(HTTP {resp.status_code}): {resp.text[:200]}"
            )
        granted = resp.json().get("permissions") or {}
        return {k: v for k, v in granted.items() if isinstance(v, str)}

    async def core_quota(self) -> tuple[int, int] | None:
        """(remaining, limit) of this installation's hourly REST quota, or None
        when GitHub did not say. `GET /rate_limit` itself does not count
        against the quota, so asking costs nothing that is being counted.
        """
        token, _ = await self.installation_token()
        async with forge_client(transport=self._transport, timeout=10.0) as client:
            resp = await client.get(
                f"{self._api_base}/rate_limit",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code != 200:
            if _unreadable_quota.get(self._installation_id) != resp.status_code:
                logger.warning(
                    "GitHub did not report installation %s's quota (HTTP %s): %s",
                    self._installation_id,
                    resp.status_code,
                    resp.text[:200],
                )
            _unreadable_quota[self._installation_id] = resp.status_code
            return None
        _unreadable_quota.pop(self._installation_id, None)
        core = resp.json().get("resources", {}).get("core", {})
        remaining, limit = core.get("remaining"), core.get("limit")
        if not isinstance(remaining, int) or not isinstance(limit, int):
            return None
        if isinstance(reset := core.get("reset"), int):
            forge_quota.record(self._installation_id, remaining, limit, float(reset))
        return remaining, limit

    async def _mint(
        self, slot: str, resolve: Callable[[], Awaitable[dict[str, str]]]
    ) -> tuple[str, str]:
        """Mint (or reuse) the installation token for one permission set.

        The set is resolved lazily, inside the cache check, so a burst of calls
        that all hit a warm token costs nothing upstream at all.

        Serialized under a lock so concurrent turns share one mint instead of
        racing GitHub for identical tokens.
        """
        async with self._lock:
            cached = self._cached.get(slot)
            if cached and cached[1] - time.time() > _REFRESH_MARGIN_S:
                token, exp = cached
                return token, _iso(exp)
            permissions = await resolve()
            async with forge_client(transport=self._transport, timeout=20.0) as client:
                resp = await client.post(
                    f"{self._api_base}/app/installations/"
                    f"{self._installation_id}/access_tokens",
                    json={"permissions": permissions} | self._scope(),
                    headers={
                        "Authorization": f"Bearer {self._app_jwt()}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            if resp.status_code != 201:
                raise GitHubAppError(
                    f"GitHub refused the token mint (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                )
            body = resp.json()
            token = body["token"]
            expires_epoch = datetime.fromisoformat(
                body["expires_at"].replace("Z", "+00:00")
            ).timestamp()
            self._cached[slot] = (token, expires_epoch)
            forge_quota.own(token, self._installation_id, expires_epoch)
            return token, _iso(expires_epoch)

    def _scope(self) -> dict:
        """Which repository the token is limited to, by id whenever it is known.

        The id, because GitHub refuses a mint naming a repository that has been
        renamed since ("There is at least one repository that does not exist or
        is not accessible", HTTP 422), while the id survives the rename. The
        name only for a binding whose id has not been learned yet.
        """
        if self._repository_id is not None:
            return {"repository_ids": [self._repository_id]}
        if self._repository:
            return {"repositories": [self._repository]}
        return {}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


_instances: dict[tuple[int, int | None, str | None], GitHubAppTokens] = {}


def _tokens_for_installation(
    installation_id: int,
    *,
    repository_id: int | None = None,
    repository: str | None = None,
) -> GitHubAppTokens | None:
    """The cached minter for one installation, or None when the App is not
    configured. One process serves every connected project, so instances are
    cached per installation and repository so tokens cannot cross project
    bindings. With neither a repository id nor a name the token reaches the
    whole installation — `locate_repo` alone asks for that, and never
    hands it on."""
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return None
    cache_key = (installation_id, repository_id, repository)
    minter = _instances.get(cache_key)
    if minter is None:
        minter = GitHubAppTokens(
            app_id=settings.github_app_id,
            private_key_path=settings.github_app_private_key_path,
            installation_id=installation_id,
            repository=repository,
            repository_id=repository_id,
        )
        _instances[cache_key] = minter
    return minter


async def github_app_tokens_for_project(
    project_id: uuid.UUID, session: AsyncSession
) -> GitHubAppTokens | None:
    """The minter for whichever installation `project_id` is connected to, or
    None if the project has no connected installation or the App is not
    configured."""
    installation = await ProjectGitInstallationRepository(session).get_by_project(
        project_id
    )
    if installation is None:
        return None
    if installation.repository_id is not None:
        return _tokens_for_installation(
            installation.installation_id, repository_id=installation.repository_id
        )
    return _tokens_for_installation(
        installation.installation_id, repository=installation.repo.split("/", 1)[1]
    )


async def locate_repo(
    installation: ProjectGitInstallation,
) -> tuple[int, str] | None:
    """Where GitHub says the bound repository is now: its id and `owner/name`.

    None when GitHub no longer knows it (deleted, or the App lost access), or
    when the App is not configured here.

    Asked by id when the id is known, which answers the same before and after
    a rename. A binding from before ids were kept is asked by its stored name:
    GitHub redirects a renamed repository's old name to the repository, so
    following the redirect finds both the id and the new name. That lookup
    cannot use a token limited to the stored name — after a rename GitHub
    refuses to mint one — so it uses one over the whole installation, which
    never leaves this function.
    """
    if installation.repository_id is None:
        return await repository_named(installation.installation_id, installation.repo)
    minter = _tokens_for_installation(
        installation.installation_id, repository_id=installation.repository_id
    )
    return await _describe(minter, f"/repositories/{installation.repository_id}")


async def repository_named(installation_id: int, name: str) -> tuple[int, str] | None:
    """The repository GitHub answers to `name` with now: its id and current
    `owner/name`, following the redirect GitHub keeps from a former name.

    With a token over the whole installation, because one limited to `name`
    is exactly what GitHub refuses to mint once `name` is a former name. The
    token never leaves this lookup.
    """
    return await _describe(_tokens_for_installation(installation_id), f"/repos/{name}")


async def _describe(
    minter: GitHubAppTokens | None, path: str
) -> tuple[int, str] | None:
    if minter is None:
        return None
    token, _ = await minter.installation_token()
    async with forge_client(
        transport=minter.transport, timeout=20.0, follow_redirects=True
    ) as client:
        resp = await client.get(
            f"{minter.api_base}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise GitHubAppError(
            f"GitHub did not describe the repository (HTTP {resp.status_code}): "
            f"{resp.text[:200]}"
        )
    body = resp.json()
    return int(body["id"]), str(body["full_name"])


async def github_app_read_token_for_project(
    project_id: uuid.UUID, session: AsyncSession
) -> str | None:
    """A read-capable installation token for `project_id`'s repo, or None when
    there is no installation to mint from.

    This is what a fetch of the project's upstream authenticates with: the
    App's own identity for a bound project, nothing at all for an unbound one
    — there is no third credential. "Read" is the full installation grant, the
    same mint a sandbox receives; the named write set (`write_token`) is for
    pushing and merging.
    """
    tokens = await github_app_tokens_for_project(project_id, session)
    if tokens is None:
        return None
    token, _expires_at = await tokens.installation_token()
    return token


async def _user_installation_items(token: str, path: str, key: str) -> list[dict]:
    """Read all pages using the user's authority, never the platform App JWT."""
    items: list[dict] = []
    async with forge_client(timeout=20.0) as client:
        page = 1
        while True:
            response = await client.get(
                f"https://api.github.com{path}",
                params={"per_page": 100, "page": page},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if response.status_code != 200:
                raise GitHubAppError(
                    "GitHub could not verify repository access "
                    f"(HTTP {response.status_code})"
                )
            batch = response.json().get(key, [])
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1


async def list_user_installations(token: str) -> list[dict]:
    return await _user_installation_items(token, "/user/installations", "installations")


async def fetch_user_installation_repos(token: str, installation_id: int) -> list[dict]:
    return await _user_installation_items(
        token, f"/user/installations/{installation_id}/repositories", "repositories"
    )
