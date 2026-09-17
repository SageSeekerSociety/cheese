"""Stable process that owns device and terminal WebSockets across backend releases."""

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

from app.api.routes.connector import router as connector_router
from app.api.routes.execution import router as execution_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.obs import configure_logging
from app.domain.agent.device_hub import DeviceOffline, device_hub
from app.domain.agent.device_hub_rpc import screen_to_json

# The same logging as the business backend: plain tracebacks rendered off the
# hot path, secrets scrubbed, application INFO lines visible. Left to structlog's
# defaults, an unhandled error here renders every frame's locals through rich on
# the event loop — the same freeze obs.py describes, in the process every
# executor call of every device goes through.
configure_logging()

_executor_calls: dict[str, asyncio.Task[dict]] = {}
_release_draining = False
_active_rpc_calls = 0
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
    "update_screen",
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
register_exception_handlers(app)


async def admit_execution_request() -> AsyncIterator[None]:
    global _active_rpc_calls
    if _release_draining:
        raise HTTPException(
            status_code=503, detail="device connection owner is draining"
        )
    _active_rpc_calls += 1
    try:
        yield
    finally:
        _active_rpc_calls -= 1


app.include_router(execution_router, dependencies=[Depends(admit_execution_request)])


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/internal/device-connection/release-drain")
async def release_drain(
    x_device_connection_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    _authorize(x_device_connection_secret)
    global _release_draining
    pending = any(
        device.exec_pending
        or device.call_pending
        or device.file_pending
        or device.executor_pending
        or device.session_pending
        for device in device_hub._devices.values()
    )
    if (
        _active_rpc_calls
        or pending
        or any(not task.done() for task in _executor_calls.values())
    ):
        raise HTTPException(status_code=409, detail="device calls are active")
    _release_draining = True
    return {"draining": True}


@app.post("/internal/device-connection/release-resume")
async def release_resume(
    x_device_connection_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    _authorize(x_device_connection_secret)
    global _release_draining
    _release_draining = False
    return {"draining": False}


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
        "active_rpc_calls": _active_rpc_calls,
        "pending_executor_traces": sum(
            not task.done() for task in _executor_calls.values()
        ),
        "device_pending": {
            device_id: {
                "exec": len(device.exec_pending),
                "call": len(device.call_pending),
                "file": len(device.file_pending),
                "executor": len(device.executor_pending),
                "session": len(device.session_pending),
            }
            for device_id, device in device_hub._devices.items()
        },
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
    global _active_rpc_calls
    if _release_draining and not _is_completed_executor_trace(name, body):
        raise HTTPException(
            status_code=503, detail="device connection owner is draining"
        )
    _active_rpc_calls += 1
    try:
        result = await _dispatch(name, body)
    except DeviceOffline as exc:
        raise HTTPException(
            status_code=409,
            detail="device offline",
            headers={"X-Device-Id": exc.device_id},
        ) from exc
    except TimeoutError as exc:
        # A device that holds a link but never answers. ``device_hub.exec``
        # waits ``timeout + 5`` on the reply future and then raises; unclaimed,
        # that reached the catch-all handler, which answers 500「服务器内部
        # 错误」 — a fault in THIS process, which is the one thing it was not.
        # The caller then reported the device's silence under its own name
        # instead: `cleanup device inventory failed device=a3dc2940aee2` with a
        # bare `Server error '500'`, three times in 90 minutes on 2026-09-16.
        #
        # No ``X-Device-Id`` here, deliberately: that header is how the client
        # tells an offline device apart from everything else, and a device that
        # is connected but silent is not offline. Sending it would turn every
        # timeout into a `DeviceOffline`, which is the opposite of describing it.
        device_id = body.get("device_id")
        named = f" {device_id}" if isinstance(device_id, str) and device_id else ""
        raise HTTPException(
            status_code=504,
            detail=f"device{named} did not answer {name} in time",
        ) from exc
    finally:
        _active_rpc_calls -= 1
    return {"result": result}


def _is_completed_executor_trace(name: str, body: dict[str, Any]) -> bool:
    if name != "call_executor":
        return False
    trace_id = body.get("trace_id")
    if not isinstance(trace_id, str):
        return False
    task = _executor_calls.get(trace_id)
    return task is not None and task.done()


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
    if name == "update_screen":
        result = device_hub.update_screen(**_uuids(body, "resource_id"))
        return screen_to_json(result)
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
