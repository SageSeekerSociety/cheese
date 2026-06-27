"""Shared secret for the sandbox-facing API (the `cheese` CLI calls these).

The sandbox container reaches the backend over the network (host.docker.internal),
so the cheese write-endpoints must NOT be open like the browser-facing ones. Each
`cheese` call carries X-Cheese-Token; the gate lives in app.main.cheese_token_gate.
"""

import secrets

from app.core.config import settings

# A per-process secret unless pinned via SANDBOX_TOKEN in the env. Injected into
# each sandbox container as CHEESE_TOKEN; the cheese CLI sends it back.
SANDBOX_TOKEN: str = settings.sandbox_token or secrets.token_hex(24)
