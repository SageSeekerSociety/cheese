"""Transport identity and evidence; no database or runtime dependencies."""

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class InputIdentity:
    project_id: uuid.UUID
    conversation_id: uuid.UUID
    recipient_handle: str
    harness: str
    native_session_id: str
    input_id: uuid.UUID
    work_id: uuid.UUID


@dataclass(frozen=True)
class InputReceipt:
    identity: InputIdentity
    evidence: Literal["accepted", "native_echo"]
    execution_work_id: uuid.UUID | None = None


@dataclass(frozen=True)
class InputEffects:
    held_block_ids: tuple[uuid.UUID, ...] = ()
    block_ids: tuple[uuid.UUID, ...] = ()
    seen_block_ids: tuple[uuid.UUID, ...] = ()
    seen_by: str | None = None
    delivery_id: uuid.UUID | None = None
    attempt_id: uuid.UUID | None = None


@dataclass(frozen=True)
class InputReconciliationPending:
    identity: InputIdentity
    accepted: bool


class InputOutcomeUnconfirmed(Exception):
    """An external call began; failure is not permission to send a new input."""

    def __init__(self, identity: InputIdentity, *, accepted: bool):
        self.identity = identity
        self.accepted = accepted
        super().__init__(
            f"Input {identity.input_id} requires reconciliation "
            f"(transport accepted={accepted})"
        )


class InputNotSent(Exception):
    """Sending stopped before anything reached the transport.

    The session could not be asked at all (not started by this process, or
    could not be started again), so the input certainly is not in it. Unlike
    :class:`InputOutcomeUnconfirmed` there is nothing to reconcile: the
    registration is withdrawn and the input may be sent again."""

    def __init__(self, identity: InputIdentity, reason: str):
        self.identity = identity
        super().__init__(reason)


class InputRegistrar(Protocol):
    """Commits an input's identity before it is sent; withdraws it when the
    send stopped before anything left (:class:`InputNotSent`)."""

    async def __call__(self, identity: InputIdentity) -> None: ...

    async def withdraw(self, identity: InputIdentity) -> None: ...

