"""Transport identity and evidence; no database or runtime dependencies."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InputIdentity:
    project_id: uuid.UUID
    topic_id: uuid.UUID
    recipient_handle: str
    harness: str
    native_session_id: str
    input_id: uuid.UUID
    work_id: uuid.UUID


@dataclass(frozen=True)
class InputReceipt:
    identity: InputIdentity
    evidence: Literal["accepted", "native_echo"]


@dataclass(frozen=True)
class InputEffects:
    block_ids: tuple[uuid.UUID, ...] = ()
    seen_block_ids: tuple[uuid.UUID, ...] = ()
    seen_by: str | None = None
    delivery_id: uuid.UUID | None = None
    attempt_id: uuid.UUID | None = None


InputRegistrar = Callable[[InputIdentity], Awaitable[None]]
ReceiptConsumer = Callable[[InputReceipt], Awaitable[None]]
