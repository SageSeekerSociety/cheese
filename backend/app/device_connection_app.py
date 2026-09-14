"""Stable process that owns device and terminal WebSockets across backend releases."""

import asyncio
import base64
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from app.api.routes.connector import router as connector_router
from app.core.config import settings
from app.domain.agent.device_hub import DeviceOffline, device_hub
from app.domain.agent.device_hub_rpc import screen_to_json

_executor_calls: dict[str, asyncio.Task[dict]] = {}
_RPC_METHODS = {
    "await_call",
    "adopt_screen",
    "call_executor",
    "call_screen",
    "close_screen",
    "exec",
    "list_screens",
    "open_screen",
    "put_file",
    "reassert_screen",
}


def _authorize(secret: str | None) -> None:
    expected = settings.device_connection_auth_secret
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="invalid device connection secret")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # Executor calls belong to this process, not to an HTTP waiter. Do not cancel
    # them during a business-backend disconnect; this process is released alone.


app = FastAPI(title="Cheese device connection owner", lifespan=lifespan)
app.include_router(connector_router)


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/internal/device-connection/snapshot")
async def snapshot(
    x_device_connection_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(x_device_connection_secret)
    devices = [
        {
            "device_id": device_id,
            "online": device_hub.is_online(device_id),
            "name": device_hub.device_name(device_id),
            "last_seen_age": device_hub.last_seen_age(device_id),
            "connection_generation": device_hub._devices[
                device_id
            ].connection_generation,
        }
        for device_id in device_hub._devices
    ]
    return {
        "devices": devices,
        "screens": [screen_to_json(screen) for screen in device_hub._screens.values()],
    }


@app.post("/internal/device-connection/call/{name}")
async def call(
    name: str,
    body: dict[str, Any],
    x_device_connection_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(x_device_connection_secret)
    if name not in _RPC_METHODS:
        raise HTTPException(status_code=404, detail="unknown device connection call")
    try:
        result = await _dispatch(name, body)
    except DeviceOffline as exc:
        raise HTTPException(
            status_code=409,
            detail="device offline",
            headers={"X-Device-Id": exc.device_id},
        ) from exc
    return {"result": result}


async def _dispatch(name: str, body: dict[str, Any]) -> Any:
    if name == "call_executor":
        trace_id = body["trace_id"]
        task = _executor_calls.get(trace_id)
        if task is None:
            if len(_executor_calls) >= 2048:
                for old_trace, old_task in list(_executor_calls.items()):
                    if old_task.done():
                        _executor_calls.pop(old_trace)
                    if len(_executor_calls) < 2048:
                        break
            task = asyncio.create_task(device_hub.call_executor(**body))
            _executor_calls[trace_id] = task
        # Shield makes an HTTP client disappearing during backend rollout unable
        # to cancel the call that the stable owner has already sent to the device.
        return await asyncio.shield(task)
    if name == "open_screen":
        result = await device_hub.open_screen(**_uuids(body, "project_id", "topic_id"))
        return screen_to_json(result)
    if name == "adopt_screen":
        result = device_hub.adopt_screen(
            **_uuids(body, "project_id", "topic_id", "resource_id")
        )
        return screen_to_json(result)
    if name == "reassert_screen":
        screen = device_hub.screen(body.pop("sid"))
        if screen is None:
            raise KeyError("screen not found")
        await device_hub.reassert_screen(screen, **body)
        return None
    if name == "put_file":
        body["data"] = base64.b64decode(body["data"], validate=True)
    method = getattr(device_hub, name)
    return await method(**body)


def _uuids(body: dict[str, Any], *names: str) -> dict[str, Any]:
    import uuid

    value = dict(body)
    for name in names:
        if value.get(name):
            value[name] = uuid.UUID(value[name])
    return value
