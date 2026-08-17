"""ccproxy tenant-realm client: one revocable ticket per device (#420).

ccproxy (`micro-teams/ccproxy`, contract `CCProxy-API.yml` there) is where the
real Anthropic credentials live — machines only ever hold a fake ticket that
ccproxy swaps per authenticated identity. Cheese is one *tenant*: it owns its
machines there, and `Authorization: Bearer <tenantSecret>` scopes every call.

The piece this buys us is revocation with a blast radius of one device.
Today every box rides a shared identity, so rotating it (or a borrower
refreshing it) kills them all at once — the 2026-08-16 dev outage. A machine
registered here gets its own identity; `DELETE /machine/{id}` is confirmed
revocation (204 = the durable credential is gone, the fake ticket can never
spend again, engine restarts included).

Registration is connector-mode: `POST /machine` WITHOUT a host. ccproxy then
hands back a `deviceToken` + one-line `installCommand` (create response only)
and waits for the machine's connector to dial in; the per-machine proxy
credential is set ON the machine by that connector, never returned through
this API. Login is per-machine and human-operated on ccproxy's side, so a
fresh machine sits `awaitingLogin` until the login operator drives the OAuth
round — callers poll `get_machine` for `has_credential` rather than assuming
a created machine can spend.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


class CcproxyTenantError(RuntimeError):
    """A ccproxy tenant call failed. Carries the status so callers can tell a
    misconfigured tenant (401/403) from a bad request from an outage."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CcproxyMachine:
    """The tenant-visible slice of ccproxy's Machine resource."""

    machine_id: int
    status: str
    online: bool
    has_credential: bool
    # Both only on the create response; None on get/list.
    device_token: str | None = None
    install_command: str | None = None
    error: str | None = None


def _machine_from(payload: dict[str, Any]) -> CcproxyMachine:
    return CcproxyMachine(
        machine_id=int(payload["id"]),
        status=str(payload.get("status") or ""),
        online=bool(payload.get("online")),
        has_credential=bool(payload.get("hasCredential")),
        device_token=payload.get("deviceToken"),
        install_command=payload.get("installCommand"),
        error=payload.get("error"),
    )


class CcproxyTenantClient:
    def __init__(
        self,
        base_url: str | None = None,
        secret: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = (base_url or settings.ccproxy_tenant_base_url).rstrip("/")
        self._secret = secret or settings.ccproxy_tenant_secret
        self._timeout = timeout or settings.ccproxy_tenant_timeout_s
        self._transport = transport

    @property
    def configured(self) -> bool:
        """Whether this deployment can talk to ccproxy at all. Without it the
        feature must report itself unavailable rather than fail per request."""
        return bool(self._base and self._secret)

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        if not self.configured:
            raise CcproxyTenantError("ccproxy is not configured for this deployment")
        url = f"{self._base}{path}"
        headers = {"Authorization": f"Bearer {self._secret}"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.request(method, url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise CcproxyTenantError(f"ccproxy unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise CcproxyTenantError(
                f"{method} {path} failed: {response.text[:300]}",
                status=response.status_code,
            )
        payload = response.json() if response.content else None
        return response.status_code, payload

    async def create_machine(self, *, label: str) -> CcproxyMachine:
        """Register a connector-mode machine (no host: cheese installs the
        connector itself, ccproxy never SSHes into our devices)."""
        _, payload = await self._call("POST", "/machine", {"label": label})
        return _machine_from(payload)

    async def get_machine(self, machine_id: int) -> CcproxyMachine:
        _, payload = await self._call("GET", f"/machine/{machine_id}")
        return _machine_from(payload)

    async def delete_machine(self, machine_id: int) -> None:
        """Confirmed revocation, not best-effort: returning at all means
        ccproxy acknowledged the credential is durably gone (204), or had
        already forgotten the machine (404 — same end state). Anything else
        raises, because a deleted device row with a live ticket is exactly
        the hazard #420 exists to close."""
        try:
            await self._call("DELETE", f"/machine/{machine_id}")
        except CcproxyTenantError as exc:
            if exc.status == 404:
                return
            raise
