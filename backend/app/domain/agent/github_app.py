"""Short-lived GitHub tokens for sandboxes (#188 minimal item 1, #192 install flow).

The platform's GitHub credential is the cheesex-app private key. It stays on
the backend and is NEVER handed to a sandbox. An agent that works on a repo
calls ``/sandbox/github-token`` with its scoped cheese token; the backend mints
an **installation access token carrying everything the App holds on that
installation** and returns that instead. GitHub expires it after an hour;
minting is cached until shortly before expiry, so a burst of calls costs one
upstream mint.

Same containment shape as the LLM gateway path (``llm_proxy``): what the
sandbox holds is short-lived, scoped to one installation, and centrally
revocable, while the long-lived secret behind it never moves. That is the
containment, and it is the whole of it — the token is deliberately NOT
narrowed below the App. An agent is a full member of the room
(``docs/agent-principles.md`` §2), so it commits, pushes and opens its PR with
the same grants the platform itself would have used on its behalf.

Which *installation* to mint from is resolved per-project (#192): the App
id and private key are one platform-wide credential, but each connected repo
has its own installation_id, looked up from ``project_git_installations``.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
# GitHub caps app JWTs at 10 minutes; stay clear of clock-skew rejections.
_JWT_TTL_S = 540
# Re-mint when the cached token has less life left than a long agent turn.
_REFRESH_MARGIN_S = 20 * 60
# How long a fetched grant map is trusted. Grants only change when a human
# edits the App, so this is really "how fast that edit reaches sandboxes".
_GRANTS_TTL_S = 10 * 60


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
        api_base: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._app_id = app_id
        self._key_path = private_key_path
        self._installation_id = installation_id
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

        This is what ``/sandbox/github-token`` hands an agent, so it has to be
        enough to finish a piece of work: commit, push the branch, open the PR,
        then read the CI it triggered. Nothing is subtracted on the way out —
        see the module docstring.
        """
        return await self._mint("installation", self.granted_permissions)

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
        async with httpx.AsyncClient(transport=self._transport, timeout=20.0) as client:
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
            async with httpx.AsyncClient(
                transport=self._transport, timeout=20.0
            ) as client:
                resp = await client.post(
                    f"{self._api_base}/app/installations/"
                    f"{self._installation_id}/access_tokens",
                    json={"permissions": permissions},
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
            return token, _iso(expires_epoch)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


_instances: dict[int, GitHubAppTokens] = {}


def _tokens_for_installation(installation_id: int) -> GitHubAppTokens | None:
    """The cached minter for one installation, or None when the App is not
    configured. One process serves every connected project, so instances are
    cached per installation_id rather than a single global one."""
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return None
    minter = _instances.get(installation_id)
    if minter is None:
        minter = GitHubAppTokens(
            app_id=settings.github_app_id,
            private_key_path=settings.github_app_private_key_path,
            installation_id=installation_id,
        )
        _instances[installation_id] = minter
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
    return _tokens_for_installation(installation.installation_id)


def _settings_app_jwt() -> str | None:
    """A 10-minute App JWT from the platform credential, or None when the App
    is not configured. App-level endpoints (``/app/installations``) take this
    directly — no installation context exists yet."""
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return None
    now = int(time.time())
    return jwt.encode(
        {
            "iat": now - 60,
            "exp": now + _JWT_TTL_S,
            "iss": str(settings.github_app_id),
        },
        Path(settings.github_app_private_key_path).read_text(),
        algorithm="RS256",
    )


async def list_app_installations() -> list[dict]:
    """Every installation of the App, via the App JWT.

    The install flow needs this because GitHub's ``installations/new`` page
    dead-ends when the App is ALREADY installed on the org: it shows the
    installation settings page and never fires the setup_url callback, so the
    signed state is lost and the platform waits forever. The connect route
    therefore looks for an existing installation itself first.
    """
    token = _settings_app_jwt()
    if token is None:
        raise GitHubAppError("GitHub App is not configured on this deployment")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            "https://api.github.com/app/installations",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if resp.status_code != 200:
        raise GitHubAppError(
            f"GitHub refused to list installations (HTTP {resp.status_code}): "
            f"{resp.text[:200]}"
        )
    return list(resp.json())


async def fetch_installation_repos(installation_id: int) -> list[dict]:
    """The repos `installation_id` can access, via its own read-only token.

    Used right after the #192 install callback: GitHub's setup_url redirect
    carries only the installation_id, not which repo(s) got connected — and
    ``/installation/repositories`` needs nothing beyond the `metadata` every
    installation grants.
    """
    minter = _tokens_for_installation(installation_id)
    if minter is None:
        raise GitHubAppError("GitHub App is not configured on this deployment")
    token, _expires_at = await minter.installation_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{minter.api_base}/installation/repositories",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if resp.status_code != 200:
        raise GitHubAppError(
            f"GitHub refused to list installation repos (HTTP {resp.status_code}): "
            f"{resp.text[:200]}"
        )
    repos = resp.json().get("repositories", [])
    return list(repos)
