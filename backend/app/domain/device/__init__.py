"""``domain/device`` — the connector device flow (architecture §5.4, Act 2 step 1).

A *device* is a client machine running the frozen ``cheese`` CLI. Enrollment is a
device flow (never a hardcoded token): the machine starts a flow, a logged-in human
approves it at ``/connect`` — binding it to a project and an agent user — and only
then does the machine receive a durable device token it presents on every later
connection.

This package is the transport-free core of that flow: a ``DeviceService`` over a
``DeviceRepository`` Protocol, with an in-memory repository for tests and the demo.
The real PostgreSQL repository is introduced in Act 3; the service does not change.
The connector routes and the ``/agent`` WebSocket (Act 2 steps 3–5) sit on top of
``verify_token`` / ``resolve_agent``.
"""

from .repository import AuthCode, Device, DeviceRepository, InMemoryDeviceRepository
from .service import DeviceService, DeviceStatus
from .sql_repository import SqlDeviceRepository

__all__ = [
    "AuthCode",
    "Device",
    "DeviceRepository",
    "DeviceService",
    "DeviceStatus",
    "InMemoryDeviceRepository",
    "SqlDeviceRepository",
]
