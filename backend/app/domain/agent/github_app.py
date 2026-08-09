"""Short-lived GitHub tokens for sandboxes (#188 minimal item 1).

The platform's GitHub credential is the cheesex-app private key. It stays on
the backend and is NEVER handed to a sandbox. A sandbox that wants to look at
CI/CD calls ``/sandbox/github-token`` with its scoped cheese token; the
backend mints an **installation access token narrowed to read-only**
(actions / checks / metadata) and returns that instead. GitHub expires it
after an hour; minting is cached until shortly before expiry, so a burst of
calls costs one upstream mint.

Same containment shape as the LLM gateway path (``llm_proxy``): the sandbox
only ever holds a credential that is short-lived, scoped, and centrally
revocable. The App's write permissions (contents / pull_requests, reserved
for PR-based accept, #188 §5.1) are NOT reachable through this module — the
narrowing happens at mint time, server-side.
"""

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt

from app.core.config import settings

# What a sandbox may do with GitHub: look, not touch.
_READONLY_PERMISSIONS = {"actions": "read", "checks": "read", "metadata": "read"}
# What the BACKEND ITSELF may do for PR-based accept (#188 §5.1): push the topic
# branch, open and merge the PR. Never exposed through any sandbox-facing route.
_WRITE_PERMISSIONS = {"contents": "write", "metadata": "read", "pull_requests": "write"}
# GitHub caps app JWTs at 10 minutes; stay clear of clock-skew rejections.
_JWT_TTL_S = 540
# Re-mint when the cached token has less life left than a long agent turn.
_REFRESH_MARGIN_S = 20 * 60


class GitHubAppError(RuntimeError):
    """Minting failed — config missing or GitHub refused."""


class GitHubAppTokens:
    """Mints (and caches) narrowed installation tokens for one installation."""

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

    async def readonly_token(self) -> tuple[str, str]:
        """A read-only installation token and its ISO expiry (sandbox-facing)."""
        return await self._mint("readonly", _READONLY_PERMISSIONS)

    async def write_token(self) -> tuple[str, str]:
        """A contents+pull_requests write token — BACKEND-INTERNAL ONLY.

        Used by PR-based accept to push the topic branch and open/merge the PR.
        No route may ever return this to a caller.
        """
        return await self._mint("write", _WRITE_PERMISSIONS)

    async def _mint(self, slot: str, permissions: dict[str, str]) -> tuple[str, str]:
        """Mint (or reuse) the installation token for one permission set.

        Serialized under a lock so concurrent turns share one mint instead of
        racing GitHub for identical tokens.
        """
        async with self._lock:
            cached = self._cached.get(slot)
            if cached and cached[1] - time.time() > _REFRESH_MARGIN_S:
                token, exp = cached
                return token, _iso(exp)
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


_instance: GitHubAppTokens | None = None


def github_app_tokens() -> GitHubAppTokens | None:
    """The process-wide minter, or None when the App is not configured."""
    global _instance
    if (
        not settings.github_app_id
        or not settings.github_app_private_key_path
        or not settings.github_app_installation_id
    ):
        return None
    if _instance is None:
        _instance = GitHubAppTokens(
            app_id=settings.github_app_id,
            private_key_path=settings.github_app_private_key_path,
            installation_id=settings.github_app_installation_id,
        )
    return _instance
