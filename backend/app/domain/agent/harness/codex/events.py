"""Translate app-server item boundaries into the shared room event vocabulary."""

from datetime import UTC, datetime

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolUse,
)


class Assembler:
    def __init__(self):
        self.pending: dict[str, AgentMessage] = {}
        self.children: dict[str, tuple[str, str]] = {}
        self.last_text: dict[str, str] = {}

    def attribution(self, thread_id: str) -> dict:
        child = self.children.get(thread_id)
        return {"agent_id": thread_id, "agent_type": child[1]} if child else {}

    def accept(self, record: dict) -> list[AgentEvent]:
        method = record["method"]
        params = record.get("params", {})
        owner = record.get("cheese", {})
        identity = {
            key: owner[key] for key in ("agent_handle", "harness") if key in owner
        }
        if method == "thread/started":
            thread = params["thread"]
            if parent := thread.get("parentThreadId"):
                role = thread.get("agentRole") or ""
                self.children[thread["id"]] = (parent, role)
                return [AgentSubagentStart(thread["id"], role, parent)]
            return [AgentSessionInfo(thread["id"], **identity)]
        if method == "turn/started":
            self.last_text.pop(params["threadId"], None)
            return []
        if method == "item/agentMessage/delta":
            eid = f"codex:{params['threadId']}:{params['itemId']}"
            message = self.pending.setdefault(
                eid, AgentMessage("", eid=eid, **self.attribution(params["threadId"]))
            )
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
                        **self.attribution(params["threadId"]),
                    )
                    return []
                message = self.pending.pop(
                    eid,
                    AgentMessage("", eid=eid, **self.attribution(params["threadId"])),
                )
                message.text = item["text"]
                message.eids = (eid,)
                self.last_text[params["threadId"]] = message.text
                return [message]
            if item["type"] == "dynamicToolCall" and method == "item/started":
                return [
                    AgentToolUse(
                        item["tool"],
                        item["arguments"],
                        eid=eid,
                        **self.attribution(params["threadId"]),
                    )
                ]
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
            thread_id = params["threadId"]
            partial = self.give_up(thread_id=thread_id)
            last = self.last_text.pop(thread_id, "")
            text = (
                error["message"]
                if error
                else (
                    messages[-1]
                    if messages
                    else (partial[-1].text if partial else last)
                )
            )
            if child := self.children.get(thread_id):
                parent, role = child
                return [
                    *partial,
                    AgentSubagentStop(
                        thread_id,
                        text,
                        role,
                        session_id=parent,
                    ),
                ]
            result = AgentResult(
                text=text,
                session_id=params["threadId"],
                is_error=failed,
                errors=[error["message"]] if error else None,
                **identity,
            )
            return [*partial, result]
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
