"""HTTP boundary between rolling backends and the stable device connection owner."""

import asyncio
import base64
import dataclasses
import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import httpx

from app.domain.agent.device_hub import DeviceOffline, HubScreen

logger = logging.getLogger(__name__)


def screen_to_json(screen: HubScreen) -> dict[str, Any]:
    return {
        "sid": screen.sid,
        "device_id": screen.device_id,
        "command": screen.command,
        "token": screen.token,
        "agent_user_id": screen.agent_user_id,
        "agent_handle": screen.agent_handle,
        "project_id": str(screen.project_id) if screen.project_id else None,
        "topic_id": str(screen.topic_id) if screen.topic_id else None,
        "resource_id": str(screen.resource_id) if screen.resource_id else None,
        "hook_key": screen.hook_key,
        "credential_expires": screen.credential_expires,
        "agent_configuration": screen.agent_configuration,
        "execution_target": screen.execution_target,
        "closing": screen.closing,
    }


def screen_from_json(value: dict[str, Any]) -> HubScreen:
    fields = {
        field.name for field in dataclasses.fields(HubScreen) if field.name != "viewers"
    }
    data = {key: value[key] for key in fields if key in value}
    for key in ("project_id", "topic_id", "resource_id"):
        if data.get(key):
            data[key] = uuid.UUID(data[key])
    return HubScreen(**data)


class RemoteDeviceHub:
    """DeviceHub-compatible client with a startup-populated read cache."""

    def __init__(
        self,
        base_url: str,
        secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"X-Device-Connection-Secret": secret}
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._devices: dict[str, dict[str, Any]] = {}
        self._screens: dict[str, HubScreen] = {}
        self._online_callback: Callable[[str], Coroutine[Any, Any, object]] | None = (
            None
        )
        self._drop_device_callback: (
            Callable[[str], Coroutine[Any, Any, object]] | None
        ) = None
        self._drop_screen_callback: (
            Callable[[HubScreen], Coroutine[Any, Any, object]] | None
        ) = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=None,
                transport=self._transport,
            )
        await self.refresh()
        if self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def close(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
            self._refresh_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                await self.refresh()
            except (httpx.HTTPError, OSError):
                # A release remains healthy enough to finish requests already in
                # flight; the next successful poll restores the read view.
                continue

    async def refresh(self) -> None:
        previous_devices = self._devices
        previous_screens = self._screens
        response = await self._request("GET", "/internal/device-connection/snapshot")
        payload = response.json()
        self._devices = {item["device_id"]: item for item in payload["devices"]}
        self._screens = {
            item["sid"]: screen_from_json(item) for item in payload["screens"]
        }
        replaced = {
            device_id
            for device_id, current in self._devices.items()
            if current["online"]
            and (previous := previous_devices.get(device_id)) is not None
            and previous["online"]
            and previous.get("connection_generation")
            != current.get("connection_generation")
        }
        for device_id, old in previous_devices.items():
            current = self._devices.get(device_id)
            if old["online"] and (
                current is None or not current["online"] or device_id in replaced
            ):
                if self._drop_screen_callback is not None:
                    for screen in previous_screens.values():
                        if screen.device_id == device_id:
                            await self._drop_screen_callback(screen)
                if self._drop_device_callback is not None:
                    await self._drop_device_callback(device_id)
        for sid, screen in previous_screens.items():
            if sid not in self._screens and self._drop_screen_callback is not None:
                await self._drop_screen_callback(screen)
        if self._online_callback is not None:
            for device_id in self.online_device_ids():
                previous = previous_devices.get(device_id)
                current = self._devices[device_id]
                if previous is None or not previous["online"] or device_id in replaced:
                    task = asyncio.create_task(self._online_callback(device_id))
                    task.add_done_callback(_log_callback_failure)

    def set_online_callback(
        self, callback: Callable[[str], Coroutine[Any, Any, object]]
    ) -> None:
        self._online_callback = callback

    def set_subscription_cleanup_callbacks(
        self,
        *,
        drop_device: Callable[[str], Coroutine[Any, Any, object]],
        drop_screen: Callable[[HubScreen], Coroutine[Any, Any, object]],
    ) -> None:
        self._drop_device_callback = drop_device
        self._drop_screen_callback = drop_screen

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._client is None:
            await self.start()
        assert self._client is not None
        response = await self._client.request(method, path, **kwargs)
        if response.status_code == 409:
            # Only the owner's 「device offline」 names the device, and reading
            # that name out of a 409 that did not carry it raised KeyError from
            # inside the transport — which then surfaced wherever the caller
            # happened to catch things, as `pi entry read failed` on a poller
            # and as a 500 on /topics/{id}/agent/control (2026-09-16, five
            # times). The KeyError described none of that, and hid whatever the
            # 409 actually said.
            #
            # So a 409 without the header falls through to raise_for_status,
            # whose HTTPStatusError carries the status and the body — the next
            # one of these says what sent it instead of being read as a device
            # going offline.
            offline = response.headers.get("X-Device-Id")
            if offline is not None:
                raise DeviceOffline(offline)
        response.raise_for_status()
        return response

    async def _call(self, name: str, payload: dict[str, Any]) -> Any:
        response = await self._request(
            "POST", f"/internal/device-connection/call/{name}", json=payload
        )
        result = response.json().get("result")
        if name in {"open_screen", "reassert_screen", "adopt_screen", "close_screen"}:
            await self.refresh()
        return result

    def is_online(self, device_id: str) -> bool:
        return bool(self._devices.get(device_id, {}).get("online"))

    def online_device_ids(self) -> list[str]:
        return [key for key, value in self._devices.items() if value["online"]]

    def device_name(self, device_id: str) -> str:
        return self._devices.get(device_id, {}).get("name") or device_id

    def last_seen_age(self, device_id: str) -> float | None:
        return self._devices.get(device_id, {}).get("last_seen_age")

    def screen_by_token(self, token: str) -> HubScreen | None:
        return next(
            (screen for screen in self._screens.values() if screen.token == token), None
        )

    def screen(self, sid: str) -> HubScreen | None:
        return self._screens.get(sid)

    def all_online_screens(self) -> list[HubScreen]:
        return [
            screen
            for screen in self._screens.values()
            if self.is_online(screen.device_id)
        ]

    def screens_in_project(self, project_id: uuid.UUID) -> list[HubScreen]:
        return [s for s in self.all_online_screens() if s.project_id == project_id]

    def screens_for_topic(self, topic_id: uuid.UUID) -> list[HubScreen]:
        return [s for s in self._screens.values() if s.topic_id == topic_id]

    async def open_screen(
        self, device_id: str, command: list[str], **kwargs: Any
    ) -> HubScreen:
        result = await self._call(
            "open_screen",
            {"device_id": device_id, "command": command, **_jsonable(kwargs)},
        )
        return screen_from_json(result)

    async def reassert_screen(self, screen: HubScreen, **kwargs: Any) -> None:
        await self._call("reassert_screen", {"sid": screen.sid, **_jsonable(kwargs)})

    async def adopt_screen(self, device_id: str, sid: str, **kwargs: Any) -> HubScreen:
        result = await self._call(
            "adopt_screen",
            {"device_id": device_id, "sid": sid, **_jsonable(kwargs)},
        )
        return screen_from_json(result)

    async def update_screen(self, sid: str, **kwargs: Any) -> HubScreen:
        result = await self._call("update_screen", {"sid": sid, **_jsonable(kwargs)})
        return screen_from_json(result)

    async def close_screen(self, device_id: str, sid: str) -> bool:
        return bool(
            await self._call("close_screen", {"device_id": device_id, "sid": sid})
        )

    async def list_screens(self, device_id: str) -> list[dict[str, Any]]:
        return await self._call("list_screens", {"device_id": device_id})

    async def call_screen(
        self, device_id: str, sid: str, name: str, args: list[Any]
    ) -> str:
        return await self._call(
            "call_screen",
            {"device_id": device_id, "sid": sid, "name": name, "args": args},
        )

    async def await_call(
        self, device_id: str, call_id: str, *, timeout: float = 30
    ) -> Any:
        return await self._call(
            "await_call",
            {"device_id": device_id, "call_id": call_id, "timeout": timeout},
        )

    async def put_file(
        self, device_id: str, sid: str, path: str, data: bytes, *, timeout: float = 30
    ) -> Any:
        return await self._call(
            "put_file",
            {
                "device_id": device_id,
                "sid": sid,
                "path": path,
                "data": base64.b64encode(data).decode(),
                "timeout": timeout,
            },
        )

    async def exec(
        self, device_id: str, argv: list[str], **kwargs: Any
    ) -> dict[str, Any]:
        return await self._call(
            "exec", {"device_id": device_id, "argv": argv, **_jsonable(kwargs)}
        )

    async def call_executor(
        self,
        device_id: str,
        state: str,
        method: str,
        params: dict,
        *,
        timeout: float = 660,
        trace_id: str | None = None,
    ) -> dict:
        trace_id = trace_id or "execution-" + uuid.uuid4().hex
        return await self._call(
            "call_executor",
            {
                "device_id": device_id,
                "state": state,
                "method": method,
                "params": params,
                "timeout": timeout,
                "trace_id": trace_id,
            },
        )


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (str(item) if isinstance(item, uuid.UUID) else item)
        for key, item in value.items()
    }


def _log_callback_failure(task: asyncio.Task[object]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 - recovery retries on the next reconnect
        logger.exception("device connection recovery callback failed")
