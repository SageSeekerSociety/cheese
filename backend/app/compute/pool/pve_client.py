"""Async Proxmox VE client for the compute machine manager (architecture doc §8).

A thin, typed wrapper over the PVE REST API covering what provisioning needs:
inventory (version / guests / pool), pick a free vmid, clone a template, and
poll the clone task. Auth is an API token (``PVEAPIToken`` header). The httpx
client is injectable, so this is unit-tested against a mock transport with no
live cluster; the token/secret come from settings (never committed).
"""

from dataclasses import dataclass
from typing import Any

import httpx


class PveError(Exception):
    pass


@dataclass(frozen=True)
class PveGuest:
    vmid: int
    node: str
    kind: str  # "qemu" | "lxc"
    name: str
    is_template: bool
    status: str


class PveClient:
    def __init__(
        self,
        base_url: str,
        token_id: str,
        token_secret: str,
        *,
        verify_ssl: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not (base_url and token_id and token_secret):
            raise PveError("PVE client requires base_url, token_id and token_secret")
        self._base = base_url.rstrip("/")
        self._auth_header = f"PVEAPIToken={token_id}={token_secret}"
        self._verify_ssl = verify_ssl
        self._client = client

    @classmethod
    def from_settings(cls, settings: Any) -> "PveClient":
        return cls(
            settings.pve_api_url,
            settings.pve_token_id,
            settings.pve_token_secret,
            verify_ssl=settings.pve_verify_ssl,
        )

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(verify=self._verify_ssl, timeout=30.0)
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base}/api2/json{path}"
        try:
            resp = await self._http().request(
                method,
                url,
                params=params,
                data=data,
                headers={"Authorization": self._auth_header},
            )
        except httpx.HTTPError as exc:
            raise PveError(f"PVE request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise PveError(f"PVE {method} {path} -> {resp.status_code}: {resp.text[:200]}")
        try:
            body = resp.json()
        except ValueError as exc:  # non-JSON 2xx body
            raise PveError(f"PVE {method} {path}: non-JSON response") from exc
        return body.get("data")

    async def version(self) -> dict[str, Any]:
        return await self._request("GET", "/version") or {}

    async def list_guests(self) -> list[PveGuest]:
        """All VMs + containers visible to the token (cluster/resources)."""
        rows = await self._request("GET", "/cluster/resources", params={"type": "vm"}) or []
        return [
            PveGuest(
                vmid=int(r["vmid"]),
                node=r.get("node", ""),
                kind=r.get("type", ""),
                name=r.get("name", ""),
                is_template=bool(r.get("template", 0)),
                status=r.get("status", ""),
            )
            for r in rows
        ]

    async def get_pool(self, poolid: str) -> dict[str, Any]:
        return await self._request("GET", f"/pools/{poolid}") or {}

    async def next_vmid(self) -> int:
        data = await self._request("GET", "/cluster/nextid")
        if data is None:
            raise PveError("PVE /cluster/nextid returned no data")
        return int(data)

    async def clone(
        self,
        *,
        node: str,
        vmid: int,
        newid: int,
        name: str,
        kind: str,
        pool: str | None = None,
        target: str | None = None,
        full: bool = True,
    ) -> str:
        """Clone template ``vmid`` into ``newid``. Returns the UPID task id to poll.

        ``kind`` is "qemu" (VM, ``name``) or "lxc" (container, ``hostname``).
        """
        if kind not in ("qemu", "lxc"):
            raise PveError(f"kind must be 'qemu' or 'lxc', got {kind!r}")
        data: dict[str, Any] = {"newid": newid, "full": 1 if full else 0}
        data["hostname" if kind == "lxc" else "name"] = name
        if pool:
            data["pool"] = pool
        if target:
            data["target"] = target
        upid = await self._request("POST", f"/nodes/{node}/{kind}/{vmid}/clone", data=data)
        if upid is None:
            raise PveError("PVE clone returned no task id")
        return str(upid)

    async def task_status(self, node: str, upid: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/status") or {}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
