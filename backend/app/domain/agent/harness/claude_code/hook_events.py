"""Claude Code hooks → AgentEvent (tmux backend).

The interactive `claude` running in a tmux session emits structured events via
Claude Code hooks. (Command hooks, not HTTP: Claude Code blocks HTTP hooks to
non-loopback targets, so a baked `cheese-hook` script reads the hook JSON on
stdin and POSTs it to /sandbox/hooks/{topic}.) This module is the pure,
docker-free core of the tmux backend:

- ``translate_hook`` maps ONE hook payload to an AgentEvent (spec §9.1: the
  platform observes 芝士 through structured events, never by parsing prose).
- ``HookRouter`` fans hook POSTs (from the /sandbox/hooks endpoint) to the
  long-lived sink owned by that topic's interactive screen.

Event mapping:
  SessionStart{session_id}                → AgentSessionInfo
  PreToolUse{tool_name, tool_input}       → AgentToolUse
  MessageDisplay{message_id,index,final,delta}
      —— MessageAssembler ——              → AgentMessage (one WHOLE message,
                                            assembled from its line-batch
                                            flushes; see the class docstring)
  PostToolUse{tool_name, tool_response}   → AgentToolResult (subagents only)
  SubagentStart{agent_id, agent_type}     → AgentSubagentStart
  SubagentStop{agent_id, last_assistant_message, agent_transcript_path}
                                          → AgentSubagentStop
  Stop{last_assistant_message, ...}       → AgentResult (ends the turn stream)
  StopFailure{error, last_assistant_message}
                                          → AgentResult(is_error=True) (ends it too:
                                            Claude Code fires this instead of Stop
                                            when the API refused the turn)

One session can have several workers going at once — a subagent's hooks come up
the same pipe as the session's own, tagged with ``agent_id`` (see ``_agent_id``).
Every event above carries that tag when the payload had one, so a reader can tell
whose work it is looking at instead of one interleaved stream from nobody.
"""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolResult,
    AgentToolUse,
    AgentUsage,
)
from app.domain.usage.tokens import input_output_tokens

#: Key a caller may stamp on a hook payload to say when the harness actually
#: recorded it. The live path leaves it off — the hook is being handled as it
#: arrives, so "now" IS the time. A spool reconcile pass sets it, because there
#: the events are minutes or hours old and "now" would file every recovered
#: message at the bottom of a conversation it belongs in the middle of.
RECORDED_AT_KEY = "_at"


def _flush_time(hook: dict) -> datetime:
    """When this hook payload happened."""
    stamped = hook.get(RECORDED_AT_KEY)
    return stamped if isinstance(stamped, datetime) else datetime.now(UTC)


# The tools whose RETURN value the room needs (see AgentToolResult): a subagent
# reports only to whoever spawned it, so without this the timeline shows the
# question and never the answer. Both names are live — `Task` is the older CLI's
# name for `Agent` and either can arrive depending on the box's image age.
_SUBAGENT_TOOLS = {"Task", "Agent"}


def _agent_id(hook: dict) -> str | None:
    """WHICH worker inside the session produced this hook — a subagent's id, or
    None for the session's own thread.

    The main thread's payloads do not carry the key at all (verified against
    2.1.224: a subagent's PreToolUse/PostToolUse carry `agent_id` and
    `agent_type`, the spawner's carry neither), so absence IS the answer rather
    than a gap: nothing has to be reconciled to decide an event belongs to the
    session. A blank value is read as absent for the same reason — an id that
    identifies nobody cannot attribute anything.
    """
    value = hook.get("agent_id")
    return value.strip() or None if isinstance(value, str) else None


def _agent_type(hook: dict) -> str | None:
    """The subagent kind (`general-purpose`, a custom agent's name…), or None."""
    value = hook.get("agent_type")
    return value.strip() or None if isinstance(value, str) else None


def _hook_event_name(hook: dict) -> str:
    """The hook's event name. Claude Code sends `hook_event_name`; accept the
    camelCase alias too so a payload-shape change doesn't silently break us."""
    return str(hook.get("hook_event_name") or hook.get("hookEventName") or "")


def _usage_from_hook(hook: dict) -> AgentUsage:
    """Best-effort token accounting from a Stop payload. Interactive hooks don't
    reliably carry usage, so this is zero unless a `usage` dict is present — the
    turn is never blocked on missing usage (design note: 拿不到就置 0)."""
    usage = hook.get("usage")
    if not isinstance(usage, dict):
        return AgentUsage()
    # Anthropic-shaped payload: cache buckets fold into input (usage.tokens).
    input_tokens, output_tokens = input_output_tokens(usage)
    return AgentUsage(
        model=str(usage.get("model") or ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=float(usage.get("cost_usd") or 0.0),
    )


def _tool_response_text(response: Any) -> str:
    """The text a tool returned, out of whichever shape Claude Code used.

    Deliberately shape-tolerant rather than shape-asserting: the payload for the
    subagent tools has been a plain string, a list of content blocks, and a dict
    wrapping that list at different CLI versions, and a hook we cannot read is
    indistinguishable in the room from a subagent that returned nothing.
    """
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, dict):
        for key in ("content", "text", "output", "result"):
            if key in response:
                return _tool_response_text(response[key])
        return ""
    if isinstance(response, list):
        parts = [_tool_response_text(item) for item in response]
        return "\n".join(part for part in parts if part).strip()
    return ""


def translate_hook(hook: dict) -> AgentEvent | None:
    """One hook payload → one AgentEvent, or None when the hook has no
    platform-visible counterpart (e.g. PostToolUse). A returned AgentResult
    signals the end of the turn (the Stop hook)."""
    event = _hook_event_name(hook)

    if event == "SessionStart":
        sid = hook.get("session_id")
        return (
            AgentSessionInfo(
                session_id=str(sid),
                agent_handle=hook.get("_agent_handle"),
                harness="claude-code",
            )
            if sid
            else None
        )

    if event == "SubagentStart":
        # No id, no event: everything downstream of this exists to attribute
        # later hooks to a worker, and an unnamed worker cannot be told apart
        # from the session — announcing one would put work on the room's
        # timeline under a name nothing else will ever match.
        agent_id = _agent_id(hook)
        if agent_id is None:
            return None
        sid = hook.get("session_id")
        return AgentSubagentStart(
            agent_id=agent_id,
            agent_type=_agent_type(hook) or "",
            session_id=str(sid) if sid else None,
        )

    if event == "SubagentStop":
        agent_id = _agent_id(hook)
        if agent_id is None:
            return None
        path = hook.get("agent_transcript_path")
        sid = hook.get("session_id")
        return AgentSubagentStop(
            agent_id=agent_id,
            text=str(hook.get("last_assistant_message") or ""),
            agent_type=_agent_type(hook) or "",
            transcript_path=str(path) if path else None,
            session_id=str(sid) if sid else None,
        )

    if event == "PreToolUse":
        tool_input = hook.get("tool_input")
        eid = hook.get("_eid")
        return AgentToolUse(
            name=str(hook.get("tool_name") or ""),
            input=tool_input if isinstance(tool_input, dict) else {},
            eid=eid if isinstance(eid, str) else None,
            agent_id=_agent_id(hook),
            agent_type=_agent_type(hook),
        )

    if event == "PostToolUse":
        # Only the subagent tools. Surfacing every tool's return would double the
        # 现场 timeline to say what its effect already says, and a Read's return
        # is the whole file — the room is for people to read.
        name = str(hook.get("tool_name") or "")
        if name not in _SUBAGENT_TOOLS:
            return None
        text = _tool_response_text(hook.get("tool_response"))
        if not text:
            return None
        tool_input = hook.get("tool_input")
        eid = hook.get("_eid")
        return AgentToolResult(
            name=name,
            text=text,
            description=(
                str(tool_input.get("description") or "")
                if isinstance(tool_input, dict)
                else ""
            ),
            eid=eid if isinstance(eid, str) else None,
            agent_id=_agent_id(hook),
            agent_type=_agent_type(hook),
        )

    if event == "MessageDisplay":
        # One FLUSH of a streaming message, not a whole message — a hook
        # STREAM must route MessageDisplay through MessageAssembler. This
        # branch survives as the one-hook-one-message fallback for payloads
        # without the flush fields (older Claude Code, hand-built tests).
        text = hook.get("delta")
        if isinstance(text, str) and text.strip():
            eid = hook.get("_eid")
            return AgentMessage(
                text=text,
                eid=eid if isinstance(eid, str) else None,
                agent_id=_agent_id(hook),
                agent_type=_agent_type(hook),
            )
        return None

    if event == "CheeseSync":
        # A machine that owns its tree reports whether its push landed. Only the
        # failure is surfaced: on success the work is already visible in the
        # branch, and a message per turn saying so would be noise that trains
        # people to skip it.
        #
        # This has to reach the human. A turn that ends with its work still on
        # the machine looks identical to one that succeeded — that is what let a
        # rejected push read as a completed turn until the machine was deleted
        # and the work went with it.
        if str(hook.get("status")) == "failed":
            branch = hook.get("branch") or "the topic branch"
            return AgentMessage(
                text=(
                    f"⚠️ 这轮的改动没能推回 {branch}——它还留在那台机器上，"
                    "采纳和 diff 现在看不到它。请重试本轮；若机器被回收，改动会丢失。"
                )
            )
        return None

    if event == "CheeseWorkspace":
        # The launcher had to repair, replace, or give up on the machine's
        # checkout before the turn could start. Always surfaced: this is the one
        # moment where the workspace an agent is about to trust is not the
        # workspace it left, and a repair that says nothing is how a turn spends
        # itself rewriting work that was still sitting there.
        detail = str(hook.get("detail") or "").strip()
        if not detail:
            return None
        return AgentMessage(
            text=(
                f"⚠️ 开工前这台机器的工作区不对劲，平台动了它：{detail}。"
                "上一轮没推出去的东西可能不在了，先确认一遍再往下写；"
                "被换掉的旧仓库放在 .git.broken.* 里，没有删。"
            )
        )

    if event == "Stop":
        sid = hook.get("session_id")
        return AgentResult(
            text=str(hook.get("last_assistant_message") or ""),
            session_id=str(sid) if sid else None,
            usage=_usage_from_hook(hook),
            agent_handle=hook.get("_agent_handle"),
            harness="claude-code",
            agent_id=_agent_id(hook),
            agent_type=_agent_type(hook),
        )

    if event == "StopFailure":
        # Fired INSTEAD of Stop when the turn ends on an API error, after Claude
        # Code's own retries ran out (10 attempts over ~3 minutes for a 429,
        # measured on 2.1.224). Without this branch the turn never ends from
        # the platform's side.
        #
        # No failure_code, on purpose. `error` is Claude Code's reading of the
        # status, and it is not reliable for what the room needs to say: a 429
        # from our own metering proxy (budget spent) arrives as
        # `authentication_failed`, because a repeated 429 from a custom gateway
        # looks like a bad key to it. So the room line is left to the text
        # path (`_turn_failure_notice`), and the error kind rides along inside
        # the text where the out-of-credit markers can still see `billing`.
        sid = hook.get("session_id")
        kind = str(hook.get("error") or "unknown")
        said = str(hook.get("last_assistant_message") or "").strip()
        text = f"{said}（{kind}）" if said else f"AI 服务拒绝了请求（{kind}）"
        return AgentResult(
            text=text,
            session_id=str(sid) if sid else None,
            is_error=True,
            errors=[kind],
            agent_handle=hook.get("_agent_handle"),
            harness="claude-code",
        )

    # Any unmapped event: nothing to surface.
    return None


@dataclass
class _PendingMessage:
    """Flushes of one streaming assistant message, keyed by flush index."""

    deltas: dict[int, str] = field(default_factory=dict)
    eids: dict[int, str | None] = field(default_factory=dict)
    final_index: int | None = None
    #: Which worker is saying this. Taken from the first flush that names one
    #: and then left alone: the flushes of ONE message all come from the same
    #: thread, so a later flush can only repeat it — while a payload that omits
    #: the key must not erase what an earlier one established, or a message
    #: assembled out of order would come out belonging to nobody.
    agent_id: str | None = None
    agent_type: str | None = None
    #: When the first flush of this message arrived — the moment 芝士 started
    #: saying it, which is where it belongs in the timeline. Assembly finishes
    #: later (a message is only known to be whole once something after it
    #: arrives), so the completion time would file it after events it actually
    #: preceded.
    started_at: datetime | None = None


class MessageAssembler:
    """Reassemble MessageDisplay flushes into whole assistant messages.

    Claude Code fires MessageDisplay once per batch of newly completed lines
    while a message streams — NOT once per message (the spike read one flush
    per message because its replies fit one batch; a 60-line reply arrives as
    ~9 flushes). The payload carries the reassembly key: ``message_id`` (stable
    across the message's flushes), ``index`` (increments per flush), ``final``
    (exactly one flush per message), and ``delta`` (the new lines, newlines
    included — concatenating deltas in index order reconstructs the message
    verbatim). Verified against 2.1.224, 2.1.233, and 2.1.261 (the pinned device
    version).

    Persisting each flush as its own chat message is what split one reply into
    several bubbles — and what then defeated every whole-text dedup downstream,
    because the Stop hook's ``last_assistant_message`` never matches a fragment,
    so the full text landed AGAIN next to its own pieces. #170 fixed the same
    shape once before by buffering fragments to a semantic boundary; this does
    the same with ``final`` as the boundary.

    Also absorbs at-least-once redelivery: a flush re-POSTed after a lost ack
    arrives with the same (message_id, index) and is dropped, whether its
    message is still pending or already assembled. Flushes may arrive out of
    order (the drainer retries a failed file while later ones already landed);
    a message completes only when every index up to ``final`` is present.

    Payloads without the flush fields (an older Claude Code) keep the
    historical one-hook-one-message behavior. One instance per hook stream
    (screen subscription / spool reconcile pass); event-loop only.
    """

    # Assembled message ids kept for late-redelivery dedup. A session streams
    # messages one at a time, so even a small window is generous.
    _DONE_CAP = 256

    def __init__(self) -> None:
        self._pending: dict[str, _PendingMessage] = {}
        self._done: dict[str, None] = {}
        self._last_stop_text: str | None = None

    def add(self, hook: dict) -> AgentMessage | None:
        """Fold one MessageDisplay payload in. Returns the completed message,
        or None while it is still streaming (or the payload was blank or a
        duplicate)."""
        delta = hook.get("delta")
        text = delta if isinstance(delta, str) else ""
        eid_value = hook.get("_eid")
        eid = eid_value if isinstance(eid_value, str) else None
        message_id = hook.get("message_id")
        final = hook.get("final")
        index = hook.get("index")
        at = _flush_time(hook)
        if (
            not isinstance(message_id, str)
            or not isinstance(final, bool)
            or not isinstance(index, int)
        ):
            if not text.strip():
                return None
            return AgentMessage(
                text=text,
                eid=eid,
                eids=(eid,) if eid else (),
                at=at,
                agent_id=_agent_id(hook),
                agent_type=_agent_type(hook),
            )
        if message_id in self._done:
            return None
        pending = self._pending.setdefault(message_id, _PendingMessage())
        if index in pending.deltas:
            return None
        pending.deltas[index] = text
        pending.eids[index] = eid
        # The tag has to survive assembly, not just translation: this is the
        # path a streamed message actually takes, and a whole reply that comes
        # out of it unattributed is one no reader can file under the worker who
        # said it.
        #
        # Measured on 2.1.224, twice (a nested claude in tmux with every hook
        # logged): a subagent's own answer produces NO MessageDisplay at all —
        # this stream carries only the main thread's display, and the
        # subagent's whole reply reached us solely as
        # SubagentStop.last_assistant_message. So nothing arrives here tagged
        # today. It stays because the cost is two fields and the failure it
        # prevents is silent: whoever changes that in Claude Code will not come
        # and tell us, and an untagged reply is indistinguishable from one the
        # session said itself.
        if pending.agent_id is None:
            pending.agent_id = _agent_id(hook)
            pending.agent_type = _agent_type(hook)
        # Earliest wins: flushes can arrive out of order (a retried spool file
        # lands after later ones), and what this records is when the message
        # STARTED, not which flush happened to be handled first.
        if pending.started_at is None or at < pending.started_at:
            pending.started_at = at
        if final:
            pending.final_index = index
        last = pending.final_index
        if last is None or any(i not in pending.deltas for i in range(last)):
            return None
        del self._pending[message_id]
        self._mark_done(message_id)
        return self._assemble(pending)

    def translate(self, hook: dict) -> list[AgentEvent]:
        """Stream-level translation of one hook payload: MessageDisplay folds
        into the assembler (a completed message emerges as ONE event), a Stop
        first drains whatever is still buffered so nothing dies with the
        buffer, and every other hook passes through ``translate_hook``."""
        name = _hook_event_name(hook)
        if name in {"UserPromptSubmit", "PreToolUse"}:
            self._last_stop_text = None
        if name == "MessageDisplay":
            message = self.add(hook)
            if (
                message is not None
                and message.agent_id is None
                and message.text.strip() == self._last_stop_text
            ):
                # A late display of the reply Stop already delivered must not
                # open a new, unsolicited turn after the session finished.
                return []
            return [message] if message is not None else []
        event = translate_hook(hook)
        if event is None:
            return []
        if isinstance(event, AgentResult):
            self._last_stop_text = None if event.is_error else event.text.strip()
            messages = self.drain()
            if messages and not event.is_error:
                last = messages[-1]
                if last.agent_id is None and event.text.strip().startswith(
                    last.text.strip()
                ):
                    # Stop carries the whole final reply when its last display
                    # flush was lost. Complete it before either copy is saved.
                    messages[-1] = replace(last, text=event.text)
            return [*messages, event]
        return [event]

    def drain(self) -> list[AgentMessage]:
        """Assemble every still-pending message from the flushes that did
        arrive (gaps collapsed), oldest first. For Stop / turn end: buffered
        content must land rather than die with the buffer."""
        drained: list[AgentMessage] = []
        for message_id, pending in self._pending.items():
            self._mark_done(message_id)
            message = self._assemble(pending)
            if message is not None:
                drained.append(message)
        self._pending.clear()
        return drained

    def pending_eids(self) -> set[str]:
        """Event ids buffered toward messages that have not completed yet —
        what a spool reconcile must NOT delete, so the flushes survive to the
        pass where their message completes."""
        return {
            eid
            for pending in self._pending.values()
            for eid in pending.eids.values()
            if eid is not None
        }

    def _mark_done(self, message_id: str) -> None:
        self._done[message_id] = None
        while len(self._done) > self._DONE_CAP:
            del self._done[next(iter(self._done))]

    @staticmethod
    def _assemble(pending: _PendingMessage) -> AgentMessage | None:
        indices = sorted(pending.deltas)
        text = "".join(pending.deltas[i] for i in indices)
        if not text.strip():
            return None
        eids = tuple(eid for i in indices if (eid := pending.eids[i]) is not None)
        return AgentMessage(
            text=text,
            eid=eids[0] if eids else None,
            eids=eids,
            at=pending.started_at,
            agent_id=pending.agent_id,
            agent_type=pending.agent_type,
        )


@dataclass(eq=False)
class HookSink:
    """One screen-lifetime hook inbox."""

    queue: asyncio.Queue[dict] = field(default_factory=asyncio.Queue)


class HookRouter:
    """Process-global router from topic id to a screen-lifetime hook sink.

    The endpoint and provider run on the same asyncio loop, so ``put_nowait`` is
    safe. Re-subscribing is idempotent: a second caller gets the existing sink
    instead of replacing it and starving its consumer.

    Which is why a topic must have exactly ONE consumer in the process. The sink
    is a queue, not a broadcast: a second consumer reading the same sink does not
    see the same hooks, it takes half of them. Half the flushes of a message
    assemble into half a reply on each side, and the "已经说过的话" a consumer
    checks the final Stop against is its own — so one sentence reaches the room
    as two fragments plus a full copy. Nothing here can enforce that (a sink does
    not know who is reading it); what does is that every channel discovers only
    its own machines, so no two of them ever recover the same topic.
    """

    def __init__(self) -> None:
        self._sinks: dict[str, HookSink] = {}

    def subscribe(self, topic_id: str) -> HookSink:
        """Return the topic's stable sink, creating it on first live screen."""
        sink = self._sinks.get(topic_id)
        if sink is None:
            sink = HookSink()
            self._sinks[topic_id] = sink
        return sink

    def unsubscribe(self, topic_id: str, sink: HookSink) -> None:
        """Release a screen's sink without evicting a newer replacement."""
        if self._sinks.get(topic_id) is sink:
            self._sinks.pop(topic_id, None)

    def push(self, topic_id: str, hook: dict) -> bool:
        """Enqueue a hook payload for the topic's subscribed screen."""
        sink = self._sinks.get(topic_id)
        if sink is None:
            return False
        sink.queue.put_nowait(hook)
        return True


# Shared singleton: the endpoint and the provider import this same instance.
hook_router = HookRouter()


def usage_from_hook(hook: dict) -> AgentUsage | None:
    """A turn's real token counts, carried back from the machine.

    Deliberately NOT part of ``translate_hook``: usage is not an event in the
    turn's stream, it is a fact about the turn. Returning it from there made the
    type checker object, and the objection was right — the caller records it,
    the UI never shows it.
    """
    # The machine is the only place these numbers exist — Claude Code writes
    # a usage block per assistant message and the transcript dies with the
    # host. Carrying them back is what turns "300 RMB went somewhere" into a
    # per-project, per-turn figure.
    # Cache reads are NOT free and they dominate; AgentUsage has no cache field,
    # so they fold into the input count (app.domain.usage.tokens — the same
    # arithmetic every supply uses).
    input_tokens, output_tokens = input_output_tokens(hook, dialect="hook")
    if input_tokens + output_tokens <= 0:
        return None
    return AgentUsage(
        model=str(hook.get("model") or "unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
