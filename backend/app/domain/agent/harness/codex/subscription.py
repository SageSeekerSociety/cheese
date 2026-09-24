"""Deliver journaled Codex events through the room's shared persistence callbacks."""

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.codex.backlog import CodexBacklog, receive
from app.domain.agent.harness.driven import subscription
from app.domain.agent.service import AgentSessionInfo


def _is_child(record: dict, reader: CodexBacklog) -> bool:
    return record.get("params", {}).get("threadId") in reader.assembler.children


class Subscription(subscription.Subscription[CodexBacklog]):
    async def receive(self) -> None:
        await receive(self.path, self.call)

    def reader(self) -> CodexBacklog:
        return CodexBacklog(self.path)

    def starts_turn(self, record: dict, reader: CodexBacklog) -> bool:
        return record["method"] == "turn/started" and not _is_child(record, reader)

    def ends_turn(self, record: dict, reader: CodexBacklog) -> bool:
        return record["method"] == "turn/completed" and not _is_child(record, reader)

    def unowned(self, entry: HarnessEvent, reader: CodexBacklog) -> None:
        # Startup precedes the first input. The runtime publishes the session
        # pointer with that input's work ID when sending it.
        if any(
            not isinstance(event, AgentSessionInfo) for event in reader.assemble(entry)
        ):
            raise RuntimeError("Codex output has no recorded room work owner")
