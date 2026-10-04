"""Which answer relay_http builds when it cannot reach the app.

The rules under test are what a viewer (and an asset client) can observe: a
navigation gets something to look at, an asset gets a transient error, and a
replaced instance is not retried on the URL that just went away. No Postgres and
no content host: the real hub, a fake transport and a real Starlette request.
"""

import uuid

import httpx
from fastapi import FastAPI, Request

from app.api.routes import app_preview
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub


async def _get(
    application: FastAPI, path: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://viewer"
    ) as browser:
        return await browser.get(path, headers=headers)


def _legacy_app(topic: uuid.UUID) -> FastAPI:
    application = FastAPI()

    @application.get("/{path:path}")
    async def viewer(request: Request):
        return await app_preview.relay_http(topic, "seat", request)

    return application


async def test_a_navigation_to_an_unreachable_app_gets_the_waiting_page(monkeypatch):
    topic = uuid.uuid4()
    hub = PreviewHub()
    application = _legacy_app(topic)
    application.state.preview_hub = hub
    # Same object as the module singleton: the legacy hub path, no instance id.
    monkeypatch.setattr(app_preview, "preview_hub", hub)

    response = await _get(
        application,
        "/",
        {"Sec-Fetch-Dest": "document", "Accept-Language": "zh-CN"},
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Cheese-Preview-State"] == "app_unavailable"
    assert response.headers["content-type"].startswith("text/html")
    assert "应用正在启动或重新连接" in response.text


async def test_an_asset_to_the_same_app_gets_a_transient_error(monkeypatch):
    topic = uuid.uuid4()
    hub = PreviewHub()
    application = _legacy_app(topic)
    application.state.preview_hub = hub
    monkeypatch.setattr(app_preview, "preview_hub", hub)

    response = await _get(application, "/main.js", {"Sec-Fetch-Dest": "script"})
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert response.headers["X-Cheese-Preview-State"] == "app_unavailable"
    assert response.text == "preview unavailable"
    assert "<html" not in response.text


async def test_a_typed_hub_with_no_tunnel_gets_the_waiting_page():
    topic = uuid.uuid4()
    # A hub that is NOT the module singleton: the typed/owner path.
    application = _legacy_app(topic)
    application.state.preview_hub = PreviewHub()

    response = await _get(application, "/", {"Sec-Fetch-Dest": "document"})
    assert response.status_code == 503
    assert response.headers["X-Cheese-Preview-State"] == "transport_unavailable"
    assert response.headers["content-type"].startswith("text/html")


class _ReplacedListener:
    """A live tunnel whose app no longer owns the bound instance."""

    machine = None

    async def send_bytes(self, data):
        op, sid, _ = wire.decode(data)
        if op == wire.OP_REQ:
            self.machine.on_frame(
                wire.encode(wire.OP_ERR, sid, b"preview instance gone")
            )


def _instance_gone_app() -> FastAPI:
    topic = uuid.uuid4()
    hub = PreviewHub()
    application = FastAPI()
    application.state.preview_hub = hub
    native = _ReplacedListener()
    native.machine = hub.attach(
        topic, "seat", native, capabilities=frozenset({"instance-v1"})
    )

    @application.get("/{path:path}")
    async def viewer(request: Request):
        return await app_preview.relay_http(topic, "seat", request, instance="a" * 64)

    return application


async def test_a_replaced_instance_navigation_is_a_restart_not_a_retry():
    response = await _get(
        _instance_gone_app(),
        "/",
        {"Sec-Fetch-Dest": "document", "Accept-Language": "zh-CN"},
    )
    assert response.status_code == 409
    assert "Retry-After" not in response.headers
    assert response.headers["X-Cheese-Preview-State"] == "instance_gone"
    assert "应用已重启，请刷新预览" in response.text
    assert "location.reload" not in response.text


async def test_a_replaced_instance_asset_keeps_the_plain_error():
    response = await _get(
        _instance_gone_app(), "/main.js", {"Sec-Fetch-Dest": "script"}
    )
    assert response.status_code == 409
    assert "Retry-After" not in response.headers
    assert response.text == "preview unavailable"
