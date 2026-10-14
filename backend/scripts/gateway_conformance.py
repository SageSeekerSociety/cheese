"""Check that the gateway serves a model the way Cheese's agents call it.

Every check sends what Claude Code (or pi, for chat_completions) actually
sends: streamed /v1/messages with Claude Code's beta headers, system layout,
adaptive thinking and effort. Each check is one failure class that reached
production before it was caught: dropped or empty tool arguments, images lost
inside tool results, thinking or effort rejected, a mid-conversation system
message dropped, the prompt cache missing, a stream that ends without its
terminal event.

    GATEWAY_KEY=sk-... python3 backend/scripts/gateway_conformance.py \\
        --base https://gateway.example [--model deepseek-flash] [--json out.json]

Without --model it checks every cheese_selectable model in /model/info.
--deployment <model_info.id> pins one deployment of a model group (LiteLLM
routes a request whose `model` is a deployment id to that deployment alone).
Requests are sequential; a model costs a few cents. Exit 1 if any check fails.

Failure reasons quote the provider's error message and a short excerpt of the
model's answer, so the output is for an operator, not a shared log.
"""

import argparse
import base64
import json
import os
import random
import struct
import sys
import time
import uuid
import zlib
from dataclasses import asdict, dataclass, field

import gateway_supply_probe as probe

# Captured from Claude Code 2.1.299 on 2026-10-14 (a request to a local
# listener with ANTHROPIC_BASE_URL pointed at it).
CLAUDE_CODE_VERSION = "2.1.299"
CLAUDE_CODE_BETAS = ",".join(
    [
        "claude-code-20250219",
        "interleaved-thinking-2025-05-14",
        "thinking-token-count-2026-05-13",
        "context-management-2025-06-27",
        "prompt-caching-scope-2026-01-05",
        "mid-conversation-tool-changes-2026-07-01",
        "inline-tools",
    ]
)
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
SYSTEM_PROMPT = (
    "You are an interactive agent that helps users with software engineering "
    "tasks. Use the tools available to you. Keep answers short."
)
REQUEST_SECONDS = 180


@dataclass
class Result:
    model: str
    check: str
    status: str
    reason: str


@dataclass
class Model:
    """What the suite needs to know about one model, from /model/info."""

    name: str
    info: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    deployment: str | None = None

    @property
    def target(self):
        """What goes in the request's `model`: a deployment id pins it."""
        return self.deployment or self.name

    def flag(self, key):
        return self.info.get(key)

    @property
    def efforts(self):
        efforts = self.info.get("cheese_efforts")
        return (
            [e for e in efforts if isinstance(e, str)]
            if isinstance(efforts, list)
            else []
        )

    @property
    def caches(self):
        """Whether reading from a prompt cache is priced, i.e. expected."""
        return any(
            source.get("cache_read_input_token_cost")
            for source in (self.params, self.info)
        )


# ---------------------------------------------------------------- streams


@dataclass
class Reply:
    """A streamed Messages response, reassembled."""

    blocks: list
    stop_reason: str | None
    usage: dict
    call_id: str | None = None

    @property
    def text(self):
        return "".join(b.get("text", "") for b in self.blocks if b["type"] == "text")

    @property
    def tool_uses(self):
        return [b for b in self.blocks if b["type"] == "tool_use"]

    @property
    def thought(self):
        return any(b["type"] in ("thinking", "redacted_thinking") for b in self.blocks)

    @property
    def prompt_tokens(self):
        return sum(
            self.usage.get(k, 0)
            for k in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )


@dataclass
class ChatReply:
    """A streamed chat-completions response, reassembled."""

    text: str
    tool_calls: list
    finish_reason: str | None
    usage: dict


def sse_events(lines):
    """JSON payloads of a server-sent event stream; `[DONE]` comes back as None."""
    data = []
    for raw in lines:
        if raw.strip():
            if raw.startswith(b"data:"):
                data.append(raw[5:].strip())
            continue
        if not data:
            continue
        payload, data = b"\n".join(data), []
        if payload == b"[DONE]":
            yield None
            continue
        try:
            yield json.loads(payload)
        except ValueError as error:
            raise probe.ProbeFailure("invalid_response") from error
    if data:
        raise probe.ProbeFailure("stream_incomplete")


def parse_messages_stream(lines):
    """Reassemble an Anthropic Messages stream. A stream that ends without
    `message_stop`, or carries an `error` event, is a failure, not a reply."""
    blocks, partial, stop_reason, usage, stopped = {}, {}, None, {}, False
    for event in sse_events(lines):
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "error":
            error = event.get("error") or {}
            raise probe.ProbeFailure(
                "provider_quota" if probe.error_code(event) else "provider_error",
                code=probe.error_code(event),
                detail=probe.error_message(event) or str(error.get("type") or ""),
            )
        if kind == "message_start":
            values = (event.get("message") or {}).get("usage") or {}
        else:
            values = event.get("usage") or {}
        for name, value in values.items():
            if isinstance(value, int) and value >= 0 and (value or name not in usage):
                usage[name] = value
        if kind == "content_block_start":
            start = event.get("content_block") or {}
            index = event.get("index", len(blocks))
            blocks[index] = {k: v for k, v in start.items() if k != "input"}
            blocks[index].setdefault("text", "")
            if start.get("type") == "tool_use":
                given = start.get("input")
                blocks[index]["input"] = json.dumps(given) if given else ""
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            block = blocks.setdefault(
                event.get("index", 0), {"type": "text", "text": ""}
            )
            if delta.get("type") == "text_delta":
                block["text"] += delta.get("text", "")
            elif delta.get("type") == "thinking_delta":
                block["text"] += delta.get("thinking", "")
            elif delta.get("type") == "input_json_delta":
                partial.setdefault(event.get("index", 0), []).append(
                    delta.get("partial_json", "")
                )
        elif kind == "message_delta":
            stop_reason = (event.get("delta") or {}).get("stop_reason", stop_reason)
        elif kind == "message_stop":
            stopped = True
            break
    if not stopped:
        raise probe.ProbeFailure("stream_incomplete")
    for index, pieces in partial.items():
        if "".join(pieces):
            blocks[index]["input"] = "".join(pieces)
    return Reply([blocks[i] for i in sorted(blocks)], stop_reason, usage)


def parse_chat_stream(lines):
    """Reassemble an OpenAI chat-completions stream. No finish_reason is a
    stream that ended early."""
    text, calls, finish, usage = [], {}, None, {}
    for event in sse_events(lines):
        if event is None:
            break
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("error"), dict):
            raise probe.ProbeFailure(
                "provider_error", detail=probe.error_message(event)
            )
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                text.append(delta["content"])
            for call in delta.get("tool_calls") or []:
                slot = calls.setdefault(
                    call.get("index", 0), {"name": "", "arguments": ""}
                )
                function = call.get("function") or {}
                slot["name"] += function.get("name") or ""
                slot["arguments"] += function.get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    if finish is None:
        raise probe.ProbeFailure("stream_incomplete")
    return ChatReply("".join(text), [calls[i] for i in sorted(calls)], finish, usage)


def stream_lines(response):
    if "text/event-stream" not in (response.headers.get("Content-Type") or ""):
        raise probe.ProbeFailure("not_streamed")
    while True:
        raw = response.readline(1 << 20)
        if not raw:
            return
        yield raw


# ---------------------------------------------------------------- requests


class Session:
    """One model's conversation with the gateway, shaped like Claude Code's."""

    def __init__(self, base, key, model, max_tokens):
        self.base, self.key, self.model = base, key, model
        self.max_tokens = max_tokens
        self.session_id = str(uuid.uuid4())

    def headers(self):
        return {
            "anthropic-beta": CLAUDE_CODE_BETAS,
            "x-app": "cli",
            "User-Agent": f"claude-cli/{CLAUDE_CODE_VERSION} (external, cli)",
            "X-Claude-Code-Session-Id": self.session_id,
        }

    def body(self, messages, tools=(), effort="high", system=SYSTEM_PROMPT):
        # Claude Code's billing line varies on every request (`cch` hashes the
        # body); the gateway strips it for non-Anthropic upstreams (#3375).
        billing = (
            f"x-anthropic-billing-header: cc_version={CLAUDE_CODE_VERSION}; "
            f"cc_entrypoint=cli; cch={random.randrange(10**5):05d}; "
            f"cc_prompt_id={uuid.uuid4()};"
        )
        return {
            "model": self.model.target,
            "max_tokens": self.max_tokens,
            "system": [
                {"type": "text", "text": billing},
                {
                    "type": "text",
                    "text": CLAUDE_CODE_IDENTITY,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            "messages": messages,
            "tools": list(tools),
            "metadata": {
                "user_id": json.dumps(
                    {
                        "device_id": "conformance",
                        "account_uuid": "",
                        "session_id": self.session_id,
                    }
                )
            },
            "thinking": {"type": "adaptive", "display": "omitted"},
            "context_management": {
                "edits": [{"type": "clear_thinking_20251015", "keep": "all"}]
            },
            "output_config": {"effort": effort},
            "stream": True,
        }

    def messages(self, payload):
        with (
            probe.deadline(REQUEST_SECONDS),
            probe.open_request(
                self.base,
                self.key,
                "/v1/messages?beta=true",
                payload,
                headers=self.headers(),
                timeout=REQUEST_SECONDS,
            ) as response,
        ):
            reply = parse_messages_stream(stream_lines(response))
            reply.call_id = probe.call_id_from(response.headers)
            return reply

    def chat(self, payload):
        with (
            probe.deadline(REQUEST_SECONDS),
            probe.open_request(
                self.base,
                self.key,
                "/v1/chat/completions",
                payload,
                timeout=REQUEST_SECONDS,
            ) as response,
        ):
            return parse_chat_stream(stream_lines(response))


def user(*blocks):
    return {
        "role": "user",
        "content": [
            b if isinstance(b, dict) else {"type": "text", "text": b} for b in blocks
        ],
    }


REMINDER = "<system-reminder>\nToday's date is 2026-10-14.\n</system-reminder>"
READ_TOOL = {
    "name": "Read",
    "description": "Reads a file from the local filesystem.",
    "input_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"file_path": {"type": "string", "description": "Absolute path"}},
        "required": ["file_path"],
        "additionalProperties": False,
    },
}
WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Current weather for one city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    },
}


def excerpt(text, limit=80):
    text = " ".join(text.split())
    return repr(text if len(text) <= limit else text[:limit] + "…")


def arguments(raw):
    """A tool call's arguments as a non-empty object, or why they are not one."""
    if not raw or not raw.strip():
        return None, "empty arguments"
    try:
        value = json.loads(raw)
    except ValueError:
        return None, f"arguments are not JSON: {excerpt(raw, 60)}"
    if not isinstance(value, dict) or not value:
        return None, f"arguments are not a non-empty object: {excerpt(raw, 60)}"
    return value, None


def finished(reply, expected="end_turn"):
    if reply.stop_reason == "max_tokens":
        return "stopped at max_tokens (reasoning may have spent it; raise --max-tokens)"
    if reply.stop_reason != expected:
        return f"stop_reason {reply.stop_reason!r}, expected {expected!r}"
    return None


# ---------------------------------------------------------------- checks
# Each check takes the Session and returns (status, reason). A request that
# fails outright (HTTP error, broken stream) is turned into a fail by run().


def stream_text(s):
    reply = s.messages(
        s.body([user(REMINDER, "Reply with the single word: pineapple")])
    )
    problem = finished(reply)
    if problem:
        return "fail", problem
    if not reply.text.strip():
        return "fail", "no text in the answer"
    if not reply.usage.get("output_tokens") or not reply.prompt_tokens:
        return "fail", f"usage missing or zero: {reply.usage}"
    return (
        "pass",
        f"{excerpt(reply.text, 30)}, {reply.prompt_tokens} in / "
        f"{reply.usage['output_tokens']} out",
    )


def tool_call(s):
    reply = s.messages(
        s.body(
            [user(REMINDER, "Read the file /etc/hostname using the Read tool.")],
            tools=[READ_TOOL],
        )
    )
    problem = finished(reply, "tool_use")
    if problem:
        return "fail", problem
    calls = [c for c in reply.tool_uses if c.get("name") == "Read"]
    if not calls:
        return "fail", f"no Read call; text {excerpt(reply.text)}"
    value, problem = arguments(calls[0].get("input"))
    if problem:
        return "fail", problem
    if not isinstance(value.get("file_path"), str) or not value["file_path"]:
        return "fail", f"file_path missing: {value}"
    return "pass", f"Read({value['file_path']})"


CITIES = ("Paris", "Tokyo", "Lima")


def parallel_tool_calls(s):
    reply = s.messages(
        s.body(
            [
                user(
                    REMINDER,
                    "Get the current weather for Paris, Tokyo and Lima. Call "
                    "get_weather three times in this one response, one call per "
                    "city, all in parallel.",
                )
            ],
            tools=[WEATHER_TOOL],
        )
    )
    problem = finished(reply, "tool_use")
    if problem:
        return "fail", problem
    calls = reply.tool_uses
    for call in calls:
        value, problem = arguments(call.get("input"))
        if problem:
            return "fail", f"call {calls.index(call) + 1} of {len(calls)}: {problem}"
        if not value.get("city"):
            return (
                "fail",
                f"call {calls.index(call) + 1} of {len(calls)}: no city in {value}",
            )
    if len(calls) < len(CITIES):
        return "warn", f"model made {len(calls)} call(s); parallel path not exercised"
    return "pass", f"{len(calls)} calls, all with arguments"


def tool_result_roundtrip(s):
    reply = s.messages(
        s.body(
            [
                user(
                    REMINDER,
                    "What number is written in /tmp/notes.txt? "
                    "Reply with the number only.",
                ),
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_conformance_1",
                            "name": "Read",
                            "input": {"file_path": "/tmp/notes.txt"},
                        }
                    ],
                },
                user(
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_conformance_1",
                        "content": "     1\tThe number is 4817.\n",
                    }
                ),
            ],
            tools=[READ_TOOL],
        )
    )
    problem = finished(reply)
    if problem:
        return "fail", problem
    if "4817" not in reply.text:
        return "fail", f"answer does not carry the tool result: {excerpt(reply.text)}"
    return "pass", "answered from the tool result"


def image_in_tool_result(s):
    if s.model.flag("supports_vision") is not True:
        return "skip", "model_info does not declare supports_vision"
    word = random.choice(sorted(WORDS))
    reply = s.messages(
        s.body(
            [
                user(
                    REMINDER,
                    "Read /tmp/label.png and tell me the word written in it. "
                    "Reply with the word only.",
                ),
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_conformance_2",
                            "name": "Read",
                            "input": {"file_path": "/tmp/label.png"},
                        }
                    ],
                },
                user(
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_conformance_2",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64.b64encode(word_png(word)).decode(),
                                },
                            }
                        ],
                    }
                ),
            ],
            tools=[READ_TOOL],
        )
    )
    problem = finished(reply)
    if problem:
        return "fail", problem
    if word not in reply.text.upper():
        return "fail", f"image word {word} not in the answer {excerpt(reply.text)}"
    return "pass", f"read {word} from the image"


def thinking_effort(s):
    if not s.model.efforts:
        return "skip", "model_info declares no cheese_efforts"
    expect = s.model.flag("supports_reasoning") is True
    seen, failures = [], []
    for effort in s.model.efforts:
        try:
            reply = s.messages(
                s.body(
                    [user(REMINDER, "What is 17 * 23? Reply with the number only.")],
                    effort=effort,
                )
            )
        except probe.ProbeFailure as error:
            failures.append(f"{effort}: {describe(error)}")
            continue
        problem = finished(reply)
        if problem:
            failures.append(f"{effort}: {problem}")
        elif expect and not reply.thought:
            failures.append(f"{effort}: no thinking block (supports_reasoning is true)")
        else:
            seen.append(f"{effort}{' thought' if reply.thought else ''}")
    if failures:
        return "fail", "; ".join(failures)
    return "pass", ", ".join(seen)


MARK = "supports_mid_conversation_system: false"
PASSPHRASES = ("heliotrope", "marzipan", "obsidian", "tamarind", "zeppelin")


def mid_conversation_system(s):
    phrase = f"{random.choice(PASSPHRASES)}-{random.randrange(100, 1000)}"
    marked = s.model.flag("supports_mid_conversation_system") is False
    try:
        reply = s.messages(
            s.body(
                [
                    user(
                        REMINDER,
                        "The passphrase is given in the system note that follows. "
                        "Reply with the passphrase only.",
                    ),
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": f"The passphrase is {phrase}.",
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    },
                ]
            )
        )
    except probe.ProbeFailure as error:
        if marked:
            return (
                "pass",
                f"rejected ({describe(error)}), and marked {MARK}",
            )
        raise
    honoured = phrase in reply.text.lower()
    if honoured and marked:
        return (
            "warn",
            f"honoured, but marked {MARK}; the mark can go",
        )
    if honoured:
        return "pass", "passphrase answered"
    if marked:
        return "pass", f"dropped, and marked {MARK}"
    return "fail", (
        f"passphrase not answered ({excerpt(reply.text)}); fix the route or mark "
        "supports_mid_conversation_system: false"
    )


def long_prefix(nonce, lines=330):
    """About 6k tokens of stable text, unique to this run so the first
    request cannot hit a cache an earlier run left warm."""
    rows = [f"Run {nonce}. Reference table for this session; do not summarise it."]
    for i in range(lines):
        rows.append(
            f"row {i:04d}: sensor {i * 37 % 1000:03d} "
            f"read {i * 7919 % 10007} at bay {i % 23}"
        )
    return "\n".join(rows)


def cache_hit(s):
    prefix = SYSTEM_PROMPT + "\n\n" + long_prefix(uuid.uuid4().hex)
    request = [user(REMINDER, "Which bay is row 0042 at? Reply with the number only.")]
    first = s.messages(s.body(request, system=prefix))
    second = s.messages(s.body(request, system=prefix))
    read = second.usage.get("cache_read_input_tokens", 0)
    # Measured against the first request's prompt, which was cold: providers
    # differ on whether input_tokens already counts the cached part.
    prompt = first.prompt_tokens or second.prompt_tokens
    share = read / prompt if prompt else 0.0
    summary = (
        f"second request read {read} of {prompt} prompt tokens from cache ({share:.0%})"
    )
    if share > 0.5:
        return "pass", summary
    if not s.model.caches:
        return "warn", summary + "; no cache-read price, so no cache expected"
    if s.model.deployment:
        return (
            "warn",
            summary + "; a pinned deployment bypasses the billing-line hook (#3375)",
        )
    return "fail", summary


def chat_completions(s):
    # pi's request (harness/pi/launch.py compat): no `store`, no `developer`
    # role, `max_tokens` rather than `max_completion_tokens`.
    base = {
        "model": s.model.target,
        "max_tokens": s.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    text = s.chat(
        {
            **base,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Reply with the single word: pineapple"},
            ],
        }
    )
    if not text.text.strip():
        return (
            "fail",
            f"text request: no content (finish_reason {text.finish_reason!r})",
        )
    tool = s.chat(
        {
            **base,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Read the file /etc/hostname using the read tool.",
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": READ_TOOL["description"],
                        "parameters": READ_TOOL["input_schema"],
                    },
                }
            ],
        }
    )
    if not tool.tool_calls:
        return (
            "fail",
            f"tool request: no tool call (finish_reason {tool.finish_reason!r}, "
            f"text {excerpt(tool.text)})",
        )
    value, problem = arguments(tool.tool_calls[0]["arguments"])
    if problem:
        return "fail", f"tool request: {problem}"
    return "pass", f"text {excerpt(text.text, 30)}, read({value.get('file_path')})"


CHECKS = {
    check.__name__: check
    for check in (
        stream_text,
        tool_call,
        parallel_tool_calls,
        tool_result_roundtrip,
        image_in_tool_result,
        thinking_effort,
        mid_conversation_system,
        cache_hit,
        chat_completions,
    )
}


def describe(error):
    if isinstance(error, probe.ProbeFailure):
        status = f" HTTP {error.status}" if error.status else ""
        detail = f": {error.detail}" if error.detail else ""
        return f"{error.category}{status}{detail}"
    return probe.classify(error)


def run(session, names):
    results = []
    for name in names:
        try:
            status, reason = CHECKS[name](session)
        except Exception as error:  # noqa: BLE001 — any failure is this check's result
            status, reason = "fail", describe(error)
        label = session.model.name
        if session.model.deployment:
            label += f"@{session.model.deployment}"
        results.append(Result(label, name, status, reason))
    return results


# ---------------------------------------------------------------- PNG text

# 5x7 glyphs, enough for WORDS.
GLYPHS = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
}
WORDS = {"ZEBRA", "MANGO", "TOGA", "BORN"}


def word_png(word, scale=8, pad=2):
    """A black-on-white PNG of ``word``, stdlib only."""
    width = (len(word) * 6 - 1 + 2 * pad) * scale
    height = (7 + 2 * pad) * scale
    rows = []
    for y in range(height):
        gy = y // scale - pad
        row = bytearray(b"\xff" * width)
        if 0 <= gy < 7:
            for n, letter in enumerate(word):
                glyph = GLYPHS[letter]
                for gx in range(5):
                    if glyph[gy][gx] == "#":
                        x0 = (pad + n * 6 + gx) * scale
                        row[x0 : x0 + scale] = b"\x00" * scale
        rows.append(b"\x00" + bytes(row))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------- catalog


def catalog(base, key):
    with (
        probe.deadline(60),
        probe.open_request(base, key, "/model/info", timeout=60) as response,
    ):
        rows = (probe.read_json(response, limit=8 << 20) or {}).get("data")
    if not isinstance(rows, list):
        raise probe.ProbeFailure("invalid_catalog")
    return [
        Model(
            row["model_name"],
            row.get("model_info") or {},
            row.get("litellm_params") or {},
        )
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("model_name"), str)
    ]


def select(rows, names, deployment):
    """The models to check: one Model per name (its first row's capabilities),
    or the one row whose model_info.id is ``deployment``."""
    if deployment:
        for row in rows:
            if row.info.get("id") == deployment:
                return [Model(row.name, row.info, row.params, deployment)]
        raise SystemExit(
            f"no deployment with model_info.id {deployment!r} in /model/info"
        )
    chosen = {}
    for row in rows:
        wanted = (
            row.name in names if names else row.info.get("cheese_selectable") is True
        )
        if wanted and row.name not in chosen:
            chosen[row.name] = row
    missing = [n for n in names if n not in chosen]
    if missing:
        raise SystemExit(f"not in /model/info: {', '.join(missing)}")
    return list(chosen.values())


def table(results):
    widths = [
        max(
            len(getattr(r, f))
            for r in results + [Result("model", "check", "status", "")]
        )
        for f in ("model", "check", "status")
    ]
    lines = []
    for r in [Result("model", "check", "status", "reason")] + results:
        cells = [
            getattr(r, f).ljust(w)
            for f, w in zip(("model", "check", "status"), widths, strict=True)
        ]
        lines.append("  ".join(cells + [r.reason]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("GATEWAY_BASE", "http://127.0.0.1:4000"),
        help="gateway base URL (env GATEWAY_BASE)",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="model name to check; repeatable. Default: every cheese_selectable model",
    )
    parser.add_argument("--deployment", help="model_info.id of one deployment to pin")
    parser.add_argument(
        "--list", action="store_true", help="list models and deployment ids, then exit"
    )
    parser.add_argument(
        "--checks", help=f"comma-separated subset of: {', '.join(CHECKS)}"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="output limit per request; reasoning models spend it thinking",
    )
    parser.add_argument(
        "--json", metavar="PATH", help="also write the results to this file"
    )
    args = parser.parse_args(argv)
    key = os.environ.get("GATEWAY_KEY") or os.environ.get("LITELLM_MASTER_KEY")
    if not key:
        parser.error("set GATEWAY_KEY (a project key) or LITELLM_MASTER_KEY")
    names = (
        list(CHECKS)
        if not args.checks
        else [n.strip() for n in args.checks.split(",") if n.strip()]
    )
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        parser.error(f"unknown checks: {', '.join(unknown)}")
    base = args.base.rstrip("/")

    try:
        rows = catalog(base, key)
    except Exception as error:  # noqa: BLE001 — reported, not raised
        print(f"cannot read /model/info: {describe(error)}", file=sys.stderr)
        return 1
    if args.list:
        for row in rows:
            print(
                row.name,
                row.info.get("id"),
                row.params.get("model"),
                "selectable" if row.info.get("cheese_selectable") is True else "",
                sep="\t",
            )
        return 0

    models = select(rows, args.model, args.deployment)
    if not models:
        print(
            "no cheese_selectable model in /model/info; name one with --model",
            file=sys.stderr,
        )
        return 1
    started, results = time.time(), []
    for model in models:
        results += run(Session(base, key, model, args.max_tokens), names)
    print(table(results))
    if args.json:
        with open(args.json, "w") as out:
            json.dump(
                {
                    "base": base,
                    "started": round(started),
                    "checks": names,
                    "results": [asdict(r) for r in results],
                },
                out,
                indent=2,
                ensure_ascii=False,
            )
    return 1 if any(r.status == "fail" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
