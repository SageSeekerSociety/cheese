"""Standalone ASGI app for the P3 connector spike (Phase A).

Mounts the REAL connector router (``/connector/auth/device/{start,poll}``,
``/connector/connect``, ``WS /connector/agent``) and the REAL sandbox hooks endpoint
(``/sandbox/hooks/{topic}``), but overrides ``get_device_service`` with an in-memory
repo so the whole slice runs without the shared Postgres / migrations and never
touches the running :8099 stack. The ``device_hub`` singleton is shared in-process, so
the orchestrator (``run_spike.py``) drives screens on the same hub the WS route feeds.

Run indirectly via ``run_spike.py`` (which starts uvicorn in-process + the cli).
"""

from fastapi import FastAPI

from app.api.routes.connector import get_device_service
from app.api.routes.connector import router as connector_router
from app.api.routes.sandbox import router as sandbox_router
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService


def build_spike_app() -> tuple[FastAPI, DeviceService]:
    """The spike app + the shared in-memory ``DeviceService`` the orchestrator uses to
    approve the device (simulating the human ``/connect`` step) and read the token."""
    device_service = DeviceService(InMemoryDeviceRepository())

    app = FastAPI(title="cheesex-connector-spike")
    app.include_router(connector_router)
    app.include_router(sandbox_router)

    # Reuse the exact connector routes, but backed by the in-memory repo (a single
    # shared instance, so the code created via HTTP /start is visible to the
    # orchestrator's direct approve and to the WS /agent token check).
    app.dependency_overrides[get_device_service] = lambda: device_service

    return app, device_service
