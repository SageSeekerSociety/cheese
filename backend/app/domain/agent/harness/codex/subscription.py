"""Deliver journaled Codex events through the room's shared persistence callbacks."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import ActivityConsumer, EventConsumer, SessionRef
from app.domain.agent.harness.codex.backlog import CodexBacklog, receive
from app.domain.agent.service import AgentResult, AgentSessionInfo


class Subscription:
    def __init__(
        self,
        session: SessionRef,
        path: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        consume: EventConsumer,
        activity: ActivityConsumer,
    ):
        self.session, self.path, self.call = session, path, call
        self.consume, self.activity = consume, activity
        self.lock = asyncio.Lock()

    async def drain(self) -> int:
        async with self.lock:
            await receive(self.path, self.call)
            reader = CodexBacklog(self.path)
            delivered = 0
            for entry in reader.unread():
                assert isinstance(entry.record, dict)
                record = entry.record
                events = reader.assemble(entry)
                work = record.get("cheese", {}).get("work_id")
                # Startup precedes the first input. The runtime publishes the
                # session pointer with that input's work ID when sending it.
                if work is None:
                    if any(not isinstance(event, AgentSessionInfo) for event in events):
                        raise RuntimeError(
                            "Codex output has no recorded room work owner"
                        )
                else:
                    work_id = uuid.UUID(work)
                    params = record.get("params", {})
                    child = params.get("threadId") in reader.assembler.children
                    if record["method"] == "turn/started" and not child:
                        await self.activity(
                            self.session.project_id,
                            self.session.topic_id,
                            work_id,
                            True,
                        )
                    for event in events:
                        await self.consume(
                            self.session.project_id,
                            self.session.topic_id,
                            work_id,
                            event,
                            getattr(event, "eid", None) or entry.eid,
                            # Completed items carry normal reply text. A result
                            # closes work without publishing the final text twice.
                            isinstance(event, AgentResult) and not event.is_error,
                            False,
                        )
                        delivered += 1
                    if record["method"] == "turn/completed" and not child:
                        await self.activity(
                            self.session.project_id,
                            self.session.topic_id,
                            work_id,
                            False,
                        )
                if not reader.unfinished():
                    reader.landed(through=entry.key)
            return delivered
