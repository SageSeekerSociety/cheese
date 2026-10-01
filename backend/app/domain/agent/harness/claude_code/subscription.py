"""Deliver journaled Claude Code records through the room's persistence callbacks.

A turn begins at the record the runner marked as its start — the echo of the
input that opened it, or the first thing a session said when nothing of ours
woke it — and ends at ``result``. An input counts as read when its echo comes
back (``--replay-user-messages``), which for words said mid-turn is the next
tool boundary: that echo, not the write, is the receipt.
"""

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import (
    EventConsumer,
    HarnessEvent,
    ReceiptConsumer,
    SessionRef,
)
from app.domain.agent.harness.claude_code.backlog import ClaudeCodeBacklog, receive
from app.domain.agent.harness.claude_code.history import landed_results
from app.domain.agent.harness.claude_code.legacy import (
    LegacyEvidenceIncomplete,
    completion_inputs,
)
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL
from app.domain.agent.harness.driven import subscription
from app.domain.agent.service import AgentEvent
from app.domain.delivery.input_identity import (
    CompletionConsumer,
    InputIdentity,
    InputReceipt,
    WorkCompletion,
)


class Subscription(subscription.Subscription[ClaudeCodeBacklog]):
    def __init__(
        self,
        session: SessionRef,
        path: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        consume: EventConsumer,
        activity: subscription.SeatActivity,
        *,
        session_id: str | None,
        recipient_handle: str,
        announce: Callable[[], Awaitable[None]],
        receipts: ReceiptConsumer | None = None,
        completions: CompletionConsumer | None = None,
        pulse: subscription.Pulse | None = None,
        memory: Callable[[], Awaitable[None]] | None = None,
        input_protocol: int | None = INPUT_PROTOCOL,
    ):
        super().__init__(
            session,
            path,
            call,
            consume,
            activity,
            receipts=receipts,
            completions=completions,
            pulse=pulse,
            memory=memory,
        )
        self.session_id = session_id
        self.recipient_handle = recipient_handle
        self.announce = announce
        self.input_protocol = input_protocol

    async def reconcile_history(self) -> None:
        """Repair old NULL completion facts without replaying landed room output.

        Pruned/incomplete intervals remain quarantined by durable admission. A
        failed database commit is not skipped: the next recovery retries it.
        """
        async with self.lock:
            for record in await self.on_disk(landed_results, self.path):
                stamp = record.get("cheese") or {}
                if not stamp.get("work_id"):
                    continue
                try:
                    echoes = await self.on_disk(
                        completion_inputs,
                        self.path,
                        work_id=stamp["work_id"],
                        session_id=self.session_id,
                        recipient_handle=self.recipient_handle,
                        result=record,
                    )
                except LegacyEvidenceIncomplete:
                    continue
                if not echoes:
                    continue
                if self.receipts is None or self.completions is None:
                    raise RuntimeError("Historical settlement consumers are not bound")
                for echo in echoes:
                    receipt = self.receipt(echo)
                    assert receipt is not None
                    await self.receipts(receipt)
                completion = WorkCompletion(
                    self.session.project_id,
                    self.session.topic_id,
                    self.recipient_handle,
                    self.session.harness,
                    self.session_id,
                    uuid.UUID(stamp["work_id"]),
                    tuple(uuid.UUID(echo["uuid"]) for echo in echoes),
                )
                current = self.completion(record)
                if current is not None and current != completion:
                    raise ValueError(
                        "Historical completion disagrees with retained inputs"
                    )
                await self.completions(completion)

    async def receive(self) -> None:
        if await receive(self.path, self.call, self.on_disk):
            await self.announce()

    def reader(self) -> ClaudeCodeBacklog:
        return ClaudeCodeBacklog(self.path, self.session_id)

    def starts_turn(self, record: dict, reader: ClaudeCodeBacklog) -> bool:
        return bool((record.get("cheese") or {}).get("turn_start"))

    def ends_turn(self, record: dict, reader: ClaudeCodeBacklog) -> bool:
        return record.get("type") == "result"

    def unowned(self, entry: HarnessEvent, reader: ClaudeCodeBacklog) -> None:
        # Before the first input, and the turns the platform runs for itself
        # (`/reload-plugins`): the session starting up says nothing the room
        # has to hear. Their facts were already taken when they were mirrored.
        reader.assemble(entry)

    def receipt(self, record: dict) -> InputReceipt | None:
        stamp = record.get("cheese") or {}
        if not stamp.get("receipt"):
            return None
        if stamp.get("receipt_session_id") != self.session_id:
            raise ValueError("Native receipt has a different session identity")
        return InputReceipt(
            InputIdentity(
                self.session.project_id,
                self.session.topic_id,
                self.recipient_handle,
                self.session.harness,
                stamp["receipt_session_id"],
                uuid.UUID(record["uuid"]),
                uuid.UUID(stamp["receipt_work_id"]),
            ),
            "native_echo",
            execution_work_id=uuid.UUID(stamp["receipt_execution_work_id"])
            if stamp.get("receipt_execution_work_id")
            else None,
        )

    async def settle_completion(self, record: dict) -> WorkCompletion | None:
        stamp = record.get("cheese") or {}
        legacy = (
            record.get("type") == "result"
            and stamp.get("work_id")
            and not record.get("is_error")
            and not stamp.get("interrupted")
            and not stamp.get("completion_input_ids")
            and (stamp.get("work_completed") or self.input_protocol != INPUT_PROTOCOL)
        )
        if not legacy:
            return await super().settle_completion(record)
        echoes = await self.on_disk(
            completion_inputs,
            self.path,
            work_id=stamp["work_id"],
            session_id=self.session_id,
            recipient_handle=self.recipient_handle,
            result=record,
        )
        if not echoes:
            return None
        if self.receipts is None or self.completions is None:
            raise RuntimeError("Legacy settlement consumers are not bound")
        for echo in echoes:
            receipt = self.receipt(echo)
            assert receipt is not None
            await self.receipts(receipt)
        completion = WorkCompletion(
            self.session.project_id,
            self.session.topic_id,
            self.recipient_handle,
            self.session.harness,
            self.session_id,
            uuid.UUID(stamp["work_id"]),
            tuple(uuid.UUID(echo["uuid"]) for echo in echoes),
        )
        await self.completions(completion)
        return completion

    def completion(self, record: dict) -> WorkCompletion | None:
        stamp = record.get("cheese") or {}
        if not stamp.get("work_completed"):
            return None
        if (
            record.get("type") != "result"
            or record.get("is_error")
            or stamp.get("interrupted")
            or not stamp.get("completion_input_ids")
            or stamp.get("completion_session_id") != self.session_id
            or record.get("session_id") != self.session_id
            or stamp.get("agent_handle") != self.recipient_handle
        ):
            raise ValueError("Native completion has a different or unfinished identity")
        return WorkCompletion(
            self.session.project_id,
            self.session.topic_id,
            self.recipient_handle,
            self.session.harness,
            stamp["completion_session_id"],
            uuid.UUID(stamp["work_id"]),
            tuple(uuid.UUID(value) for value in stamp["completion_input_ids"]),
        )

    def marks(self, record: dict, events: list[AgentEvent]) -> set[str]:
        marks = subscription.marks_of(events)
        message = (
            record.get("entry") if record.get("type") == "cheese_file" else record
        ) or {}
        # A tool comes back in a user record. Other records carry a `message`
        # of their own shape: a refused permission's is a plain string.
        if message.get("type") != "user":
            return marks
        content = (message.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                marks |= {
                    subscription.PROGRESS,
                    subscription.returned(str(block.get("tool_use_id"))),
                }
        return marks
