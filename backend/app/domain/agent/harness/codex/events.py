"""Translate app-server item boundaries into the shared room event vocabulary."""

from datetime import UTC, datetime

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentToolUse,
)


class Assembler:
    def __init__(self):
        self.pending: dict[str, AgentMessage] = {}

    def accept(self, record: dict) -> list[AgentEvent]:
        method = record["method"]
        params = record.get("params", {})
        if method == "thread/started":
            return [AgentSessionInfo(params["thread"]["id"])]
        if method == "item/agentMessage/delta":
            eid = f"codex:{params['threadId']}:{params['itemId']}"
            message = self.pending.setdefault(eid, AgentMessage("", eid=eid))
            message.text += params["delta"]
            return []
        if method in ("item/started", "item/completed"):
            item = params["item"]
            eid = f"codex:{params['threadId']}:{item['id']}"
            if item["type"] == "agentMessage":
                if method == "item/started":
                    at = params.get("startedAtMs", record.get("emittedAtMs"))
                    self.pending[eid] = AgentMessage(
                        item.get("text", ""),
                        eid=eid,
                        at=datetime.fromtimestamp(at / 1000, UTC) if at else None,
                    )
                    return []
                message = self.pending.pop(eid, AgentMessage("", eid=eid))
                message.text = item["text"]
                message.eids = (eid,)
                return [message]
            if item["type"] == "dynamicToolCall" and method == "item/started":
                return [AgentToolUse(item["tool"], item["arguments"], eid=eid)]
            return []
        if method == "turn/completed":
            turn = params["turn"]
            messages = [
                item["text"]
                for item in turn.get("items", [])
                if item["type"] == "agentMessage"
            ]
            error = turn.get("error")
            failed = turn["status"] == "failed"
            result = AgentResult(
                text=error["message"] if error else (messages[-1] if messages else ""),
                session_id=params["threadId"],
                is_error=failed,
                errors=[error["message"]] if error else None,
            )
            return [*self.give_up(thread_id=params["threadId"]), result]
        return []

    def give_up(self, *, thread_id: str | None = None) -> list[AgentMessage]:
        keys = [
            key
            for key in self.pending
            if thread_id is None or key.startswith(f"codex:{thread_id}:")
        ]
        messages = [self.pending.pop(key) for key in keys]
        messages = [message for message in messages if message.text]
        for message in messages:
            message.eids = (message.eid,) if message.eid else ()
        return messages
