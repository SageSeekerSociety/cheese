"""MicroCloud tenant-realm client.

MicroCloud is the team's IaaS control plane (`micro-teams/micro-cloud`). Its
contract — `MicroCloud-API.yml` there — splits a physical layer only its
operators see from a small logical one callers use: an *offering* is a
(machine type, zone, template) triple granted to a tenant, and a *machine* is
created from one. cheese is one tenant and authenticates with an opaque secret.

Everything Proxmox-shaped is asynchronous: create/start/stop/delete are accepted
and the caller polls. This client therefore never waits — callers own the
polling, so a slow Proxmox task can't hold an HTTP request open.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx

from app.core.config import settings

_http_clients: dict[tuple[str, str], httpx.AsyncClient] | None = None


@asynccontextmanager
async def reuse_connections() -> AsyncIterator[None]:
    """Keep tenant connections open until the application has stopped its jobs."""
    global _http_clients
    previous = _http_clients
    clients: dict[tuple[str, str], httpx.AsyncClient] = {}
    _http_clients = clients
    try:
        yield
    finally:
        _http_clients = previous
        async with AsyncExitStack() as closing:
            for client in clients.values():
                closing.push_async_callback(client.aclose)


class MicroCloudError(RuntimeError):
    """A MicroCloud call failed. Carries the status so callers can tell a
    misconfigured tenant (401/403) from a bad request from an outage."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MicroCloudClient:
    def __init__(
        self,
        base_url: str | None = None,
        secret: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base = (base_url or settings.microcloud_base_url).rstrip("/")
        self._secret = secret or settings.microcloud_tenant_secret
        self._timeout = timeout or settings.microcloud_timeout_s

    @property
    def configured(self) -> bool:
        """Whether this deployment can talk to MicroCloud at all. Without it the
        feature must report itself unavailable rather than fail per request."""
        return bool(self._base and self._secret)

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        if not self.configured:
            raise MicroCloudError("MicroCloud is not configured for this deployment")
        url = f"{self._base}/microcloud{path}"
        headers = {"Authorization": f"Bearer {self._secret}"}
        try:
            if _http_clients is None:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method, url, json=body, headers=headers
                    )
            else:
                # Cookies and connection state must not cross tenant credentials.
                key = (self._base, self._secret)
                client = _http_clients.get(key)
                if client is None:
                    client = httpx.AsyncClient(timeout=self._timeout)
                    _http_clients[key] = client
                response = await client.request(
                    method, url, json=body, headers=headers, timeout=self._timeout
                )
        except httpx.HTTPError as exc:
            raise MicroCloudError(f"MicroCloud unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise MicroCloudError(
                f"{method} {path} failed: {response.text[:300]}",
                status=response.status_code,
            )
        if not response.content:
            return None
        return response.json()

    async def claim_warm_machine(
        self, machine_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._call("POST", f"/machine/{machine_id}/claim", body)

    # --- offerings -------------------------------------------------------

    async def list_offerings(self) -> list[dict[str, Any]]:
        data = await self._call("GET", "/machine/offering?page_size=100")
        return (data or {}).get("items", [])

    # --- customers & accounts --------------------------------------------

    async def find_customer(self, external_ref: str) -> dict[str, Any] | None:
        data = await self._call("GET", "/customer?page_size=100")
        for item in (data or {}).get("items", []):
            if item.get("externalRef") == external_ref:
                return item
        return None

    async def create_customer(self, external_ref: str) -> dict[str, Any]:
        return await self._call("POST", "/customer", {"externalRef": external_ref})

    async def find_account(self, customer_id: int, name: str) -> dict[str, Any] | None:
        data = await self._call(
            "GET", f"/account?customer_id={customer_id}&page_size=100"
        )
        for item in (data or {}).get("items", []):
            if item.get("name") == name:
                return item
        return None

    async def create_account(self, customer_id: int, name: str) -> dict[str, Any]:
        return await self._call(
            "POST", "/account", {"customerId": customer_id, "name": name}
        )

    async def topup(
        self, account_id: int, amount: float, remark: str = ""
    ) -> dict[str, Any]:
        return await self._call(
            "POST", f"/account/{account_id}/topup", {"amount": amount, "remark": remark}
        )

    # --- machines ---------------------------------------------------------

    async def create_machine(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/machine", body)

    async def switch_ai(self, machine_id: int, mode: str) -> dict[str, Any]:
        """Point the machine's built-in AI channel at ``ccproxy``/``newapi``
        (the tenant console's →ccproxy button). Asynchronous on MicroCloud's
        side: aiStatus drops back to provisioning and settles on its own."""
        return await self._call("POST", f"/machine/{machine_id}/ai/{mode}")

    async def find_machine(
        self, customer_id: int, hostname: str
    ) -> dict[str, Any] | None:
        """The customer's machine with this hostname, if the provider has one."""
        data = await self._call(
            "GET", f"/machine?customerId={customer_id}&page_size=100"
        )
        for item in (data or {}).get("items", []):
            if item.get("hostname") == hostname:
                return item
        return None

    async def get_machine(self, machine_id: int) -> dict[str, Any] | None:
        """None when MicroCloud no longer knows the machine — a deleted machine
        404s, which is a normal terminal outcome, not an error."""
        try:
            return await self._call("GET", f"/machine/{machine_id}")
        except MicroCloudError as exc:
            if exc.status == 404:
                return None
            raise

    async def delete_machine(self, machine_id: int) -> None:
        try:
            # Powering a machine down is NOT `/machine/{id}/stop`: that is a hard
            # cut with no flush (MicroCloud 0.4.0), and this platform's agents keep
            # uncommitted work on the machine between turns. `/shutdown` is the
            # graceful one. Delete is used here because the machine is going away
            # entirely, not being parked.
            await self._call("DELETE", f"/machine/{machine_id}")
        except MicroCloudError as exc:
            # Already gone is the outcome the caller wanted.
            if exc.status != 404:
                raise
