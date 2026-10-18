"""Adapt OpenCode's free agent endpoint after LiteLLM's protocol translation."""

import copy
import hashlib
import inspect
import json
import secrets
import string
import time
from collections import OrderedDict
from functools import wraps
from urllib.parse import urlsplit

import httpx

_sessions = OrderedDict()
_last_timestamp = 0
_counter = 0
_alphabet = string.ascii_letters + string.digits


def _id(prefix):
    global _last_timestamp, _counter
    timestamp = int(time.time() * 1000)
    # Match OpenCode's per-millisecond sequence: the free endpoint rejects
    # sessions whose sequence has accumulated across earlier requests.
    _counter = _counter + 1 if timestamp == _last_timestamp else 1
    _last_timestamp = timestamp
    value = (timestamp * 4096 + _counter) & ((1 << 48) - 1)
    if prefix == "ses":
        value ^= (1 << 48) - 1
    return f"{prefix}_{value:012x}" + "".join(secrets.choice(_alphabet) for _ in range(14))


def _session(body, headers, logging_obj):
    from cheese_upstream_headers import derived_session

    params = getattr(logging_obj, "litellm_params", None) or {}
    metadata = params.get("litellm_metadata") or params.get("metadata") or {}
    seed = headers.get("x-opencode-session") or derived_session({"messages": body.get("messages"),
                                                               "metadata": metadata})
    key = metadata.get("user_api_key_hash") or metadata.get("user_api_key") or ""
    fingerprint = hashlib.sha256(json.dumps([str(key), seed]).encode()).hexdigest()
    if fingerprint not in _sessions:
        _sessions[fingerprint] = _id("ses")
        if len(_sessions) > 1000:
            _sessions.popitem(last=False)
    _sessions.move_to_end(fingerprint)
    return _sessions[fingerprint]


def _tools(body):
    tools = body.setdefault("tools", [])
    names = {t.get("function", {}).get("name") for t in tools}
    aliases = {name: name.lower() for name in ("Bash", "Read")
               if name in names and name.lower() not in names}
    missing = {name for name in ("bash", "read") if name not in names and name not in aliases.values()}
    for tool in tools:
        function = tool.get("function", {})
        function["name"] = aliases.get(function.get("name"), function.get("name"))
    for message in body.get("messages", []):
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            if function.get("name") in aliases:
                function["name"] = aliases[function["name"]]
    choice = body.get("tool_choice")
    if isinstance(choice, dict):
        function = choice.get("function", {})
        if function.get("name") in aliases:
            function["name"] = aliases[function["name"]]
    for name in sorted(missing):
        # The free endpoint requires both schemas even for callers without tools.
        # Calling an unavailable tool is an error, never a successful empty reply.
        tools.append({"type": "function", "function": {
            "name": name, "description": "Unavailable in this session. Never call this tool.",
            "parameters": {"type": "object", "properties": {
                "unavailable": {"type": "boolean", "description": "This tool is unavailable."}},
                "required": ["unavailable"], "additionalProperties": False},
        }})
    return {v: k for k, v in aliases.items()}, missing


class AgentStream(httpx.AsyncByteStream):
    def __init__(self, response, aliases, missing):
        self.reader = httpx.Response(response.status_code, headers=response.headers,
                                     stream=response.stream, request=response.request)
        self.aliases = aliases
        self.missing = missing
        self.closed = False
        self.choices = set()
        self.finished = set()

    def event(self, lines):
        data = "\n".join(line[5:].lstrip(" ") for line in lines if line.startswith("data:"))
        if data == "[DONE]" and self.choices - self.finished:
            raise httpx.RemoteProtocolError("OpenCode free ended an incomplete response")
        if not data or data == "[DONE]":
            return ("\n".join(lines) + "\n\n").encode()
        try:
            chunk = json.loads(data)
        except ValueError as exc:
            raise httpx.RemoteProtocolError("OpenCode free returned invalid SSE JSON") from exc
        if chunk.get("error"):
            raise httpx.RemoteProtocolError("OpenCode free returned a streaming error")
        for choice in chunk.get("choices", []):
            index = choice.get("index", 0)
            self.choices.add(index)
            if choice.get("finish_reason") is not None:
                self.finished.add(index)
            for call in choice.get("delta", {}).get("tool_calls") or []:
                function = call.get("function", {})
                name = function.get("name")
                if name in self.missing:
                    raise httpx.RemoteProtocolError(f"OpenCode free called unavailable tool: {name}")
                if name in self.aliases:
                    function["name"] = self.aliases[name]
        other = [line for line in lines if not line.startswith("data:")]
        parts = [chunk]
        choices = chunk.get("choices", [])
        if len(choices) == 1:
            choice = choices[0]
            calls = choice.get("delta", {}).get("tool_calls") or []
            if len(calls) > 1:
                # The pinned Messages converter concatenates batched calls.
                # Separate their events while emitting text and usage once.
                parts = []
                for index, call in enumerate(calls):
                    delta = dict(choice["delta"]) if index == 0 else {}
                    delta["tool_calls"] = [call]
                    last = index == len(calls) - 1
                    part = {**chunk, "choices": [{**choice, "delta": delta,
                             "finish_reason": choice.get("finish_reason") if last else None}]}
                    if not last:
                        part.pop("usage", None)
                    if index:
                        part["choices"][0].pop("logprobs", None)
                    parts.append(part)
        return b"".join(("\n".join([*other, "data: " + json.dumps(part)]) + "\n\n").encode() for part in parts)

    async def __aiter__(self):
        lines = []
        try:
            async for line in self.reader.aiter_lines():
                if line:
                    lines.append(line)
                elif lines:
                    yield self.event(lines)
                    lines = []
            if lines:
                yield self.event(lines)
            if not self.choices or self.choices - self.finished:
                raise httpx.RemoteProtocolError("OpenCode free ended an incomplete response")
        finally:
            await self.aclose()

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.reader.aclose()


async def _buffer(response):
    from litellm import stream_chunk_builder
    from litellm.types.utils import ModelResponseStream

    chunks = []
    try:
        async for line in response.aiter_lines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                chunks.append(ModelResponseStream(**json.loads(line[5:])))
        if not chunks:
            raise httpx.RemoteProtocolError("OpenCode free returned an empty stream")
        result = stream_chunk_builder(chunks)
        if result is None:
            raise httpx.RemoteProtocolError("OpenCode free returned an incomplete stream")
        headers = dict(response.headers)
        headers.pop("content-type", None)
        headers.pop("transfer-encoding", None)
        return httpx.Response(response.status_code, headers=headers, json=result.model_dump(exclude_none=True),
                              request=response.request, extensions=response.extensions)
    finally:
        await response.aclose()


def adapt_opencode_free(post):
    signature = inspect.signature(post)

    @wraps(post)
    async def adapted(self, url, *args, **kwargs):
        endpoint = urlsplit(str(url))
        if (endpoint.scheme, endpoint.hostname, endpoint.path) != (
            "https", "opencode.ai", "/inference/openai/v1/chat/completions"
        ):
            return await post(self, url, *args, **kwargs)
        bound = signature.bind(self, url, *args, **kwargs)
        values = bound.arguments
        field = next((key for key in ("json", "data", "content") if values.get(key) is not None), None)
        if field is None:
            return await post(*bound.args, **bound.kwargs)
        try:
            raw = values[field]
            body = copy.deepcopy(raw) if isinstance(raw, dict) else json.loads(raw)
        except (TypeError, ValueError):
            return await post(*bound.args, **bound.kwargs)
        if not isinstance(body, dict):
            return await post(*bound.args, **bound.kwargs)
        model = body.get("model") or ""
        if not (model.endswith("-free") or model == "big-pickle"):
            return await post(*bound.args, **bound.kwargs)
        streaming = bool(values.get("stream") or body.get("stream"))
        aliases, missing = _tools(body)
        headers = httpx.Headers(values.get("headers") or {})
        session = _session(body, headers, values.get("logging_obj"))
        headers.update({
            "user-agent": "opencode/1.19.3 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14",
            "x-opencode-client": "cli", "x-opencode-project": "global",
            "x-opencode-session": session, "x-opencode-session-id": session,
            "x-opencode-request": _id("msg"), "accept-encoding": "identity",
        })
        headers.setdefault("authorization", "Bearer public")
        headers.pop("content-length", None)
        body["stream"] = True
        values[field] = body if field == "json" else json.dumps(body)
        values["headers"] = dict(headers)
        values["stream"] = True
        response = await post(*bound.args, **bound.kwargs)
        response.stream = AgentStream(response, aliases, missing)
        response.headers.pop("content-encoding", None)
        response.headers.pop("content-length", None)
        return response if streaming else await _buffer(response)

    return adapted
