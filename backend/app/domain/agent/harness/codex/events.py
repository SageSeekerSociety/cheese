"""Translate app-server item boundaries into the shared room event vocabulary."""

import re
from datetime import UTC, datetime

from app.domain.agent.service import (
    STEP_ERROR_MAX,
    AgentCompacting,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentSessionInfo,
    AgentStepFailed,
    AgentStepOutput,
    AgentToolUse,
)

#: "attempt/limit" inside the message of an `error` notification that will be
#: retried.
RETRY_COUNT = re.compile(r"(\d+)\s*/\s*(\d+)")


class Assembler:
    def __init__(self):
        self.pending: dict[str, AgentMessage] = {}
        #: Threads a thread started (``parentThreadId``). Their turns are the
        #: session's own work: a child's completion is not the session's end.
        self.children: set[str] = set()
        self.last_text: dict[str, str] = {}

    def accept(self, record: dict) -> list[AgentEvent]:
        method = record["method"]
        params = record.get("params", {})
        owner = record.get("cheese", {})
        identity = {
            key: owner[key] for key in ("agent_handle", "harness") if key in owner
        }
        message_owner = {"agent_handle": owner.get("agent_handle")}
        if method == "thread/started":
            thread = params["thread"]
            if thread.get("parentThreadId"):
                self.children.add(thread["id"])
                return []
            return [AgentSessionInfo(thread["id"], **identity)]
        if method == "turn/started":
            self.last_text.pop(params["threadId"], None)
            return []
        if method == "error" and params.get("willRetry"):
            # The app-server's only word on a retry: the message the build
            # wrote for it ("Reconnecting... 2/5"). The counters are read out
            # of that line when it has them; a failure that will NOT be retried
            # is the turn's to report, in `turn/completed`.
            said = str((params.get("error") or {}).get("message") or "")
            counted = RETRY_COUNT.search(said)
            return [
                AgentRetrying(
                    error=said,
                    attempt=int(counted[1]) if counted else None,
                    max_attempts=int(counted[2]) if counted else None,
                )
            ]
        if method == "item/agentMessage/delta":
            eid = f"codex:{params['threadId']}:{params['itemId']}"
            message = self.pending.setdefault(
                eid,
                AgentMessage("", eid=eid, **message_owner),
            )
            message.text += params["delta"]
            return []
        if method in ("item/started", "item/completed"):
            item = params["item"]
            eid = f"codex:{params['threadId']}:{item['id']}"
            if item["type"] == "contextCompaction":
                # The thread's history is being summarised (codex 0.154.0 writes
                # it as an item of its own). The item says nothing about how it
                # went: a compaction that failed fails the turn, and the turn's
                # ending says so.
                return [AgentCompacting(done=method == "item/completed")]
            if item["type"] == "agentMessage":
                if method == "item/started":
                    at = params.get("startedAtMs", record.get("emittedAtMs"))
                    self.pending[eid] = AgentMessage(
                        item.get("text", ""),
                        eid=eid,
                        at=datetime.fromtimestamp(at / 1000, UTC) if at else None,
                        **message_owner,
                    )
                    return []
                message = self.pending.pop(
                    eid,
                    AgentMessage("", eid=eid, **message_owner),
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
                        # The item's own id: its completion names the same one.
                        call_id=item["id"],
                    )
                ]
            if item["type"] == "dynamicToolCall":
                said = "\n".join(
                    part.get("text", "")
                    for part in item.get("contentItems") or []
                    if isinstance(part, dict) and part.get("type") == "inputText"
                )
                steps: list[AgentEvent] = []
                # The call's answer said it failed (`success: false`, which the
                # platform's own tool bridge sets), or the item did.
                if item.get("success") is False or item.get("status") == "failed":
                    steps.append(
                        AgentStepFailed(
                            call_id=item["id"],
                            text=" ".join(said.split())[-STEP_ERROR_MAX:],
                        )
                    )
                if said.strip():
                    steps.append(AgentStepOutput(call_id=item["id"], text=said))
                return steps
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
            if thread_id in self.children:
                return [*partial]
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
