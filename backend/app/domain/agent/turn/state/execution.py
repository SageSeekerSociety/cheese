"""Facts shared by the scheduler owner and its frame executor."""

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass

from app.core.errors import BaseError


@dataclass(frozen=True, slots=True)
class ExecutionState:
    # References to the owner's actual tables, not copied liveness snapshots.
    recent: deque[dict]
    live: dict[str, asyncio.Task]
    last_frame_at: dict[str, float]
    live_topics: dict[str, uuid.UUID]
    delivered: set[str]
    host_failed_topics: set[str]


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    timeout: float
    first_output_timeout: float
    credential_expired_fuse: float
    resend_reason: str
    expected_error_types: tuple[type[BaseError], ...]
