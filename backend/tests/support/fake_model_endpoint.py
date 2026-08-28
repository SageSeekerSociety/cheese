"""A local OpenAI-compatible endpoint that stands in for the Zhipu key.

OpenViking needs two OpenAI-protocol endpoints: an embedding one (vectors for
its index) and a chat one (the extractor's ReAct loop). Both are the same
Zhipu key in production. This module serves both locally so the whole
``memory_backend="openviking"`` path — ov.conf, embedded AGFS, extraction,
vector index, semantic search — can be exercised with no key and no network.

What is faithful and what is not:

- Embeddings ARE real vectors, just not learned ones: a hashed character
  n-gram bag, L2-normalized. Cosine similarity therefore tracks lexical
  overlap. Retrieval works; it is weaker than a trained model, never absent.
- Chat is a scripted extractor, not a model. It reads the JSON Schema that
  ``ExtractLoop`` embeds in its system prompt and returns one minimal valid
  memory item carrying the conversation text. So the storage/index/search
  path is exercised for real; the *quality* of what gets extracted is not.

Run standalone (e.g. to point a dev backend at it):

    uv run python -m tests.support.fake_model_endpoint --port 8799
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, Request

# The extractor's system prompt ends with:  ## Output Format ... ```json <schema> ```
_SCHEMA_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
# The session transcript, as the two callers wrap it.
_SESSION_CONTENT = re.compile(r"<session_content>(.*?)</session_content>", re.DOTALL)
_HISTORY_LINE = re.compile(r"^\[\d+\]\[[^\]]*\]\[[^\]]*\]:\s*(.*)$")
# Body fields: free prose lives here. Everything else required by an item
# schema is an identity field that OpenViking turns into the card's file name,
# so it has to stay short and path-safe.
_BODY_FIELDS = frozenset(
    {"content", "summary", "goal", "detail", "details", "description", "text", "body"}
)
_UNSAFE_IN_NAME = re.compile(r"[^\w一-鿿-]+")


# --- embeddings -----------------------------------------------------------


def fake_embedding(text: str, dimension: int) -> list[float]:
    """Deterministic hashed-n-gram vector. Similar text → similar vector."""
    vec = [0.0] * dimension
    norm = " ".join(str(text).split()).lower()
    grams = [norm[i : i + 3] for i in range(max(len(norm) - 2, 1))]
    for gram in grams:
        digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    length = math.sqrt(sum(v * v for v in vec))
    if length == 0.0:
        # Empty input still needs a unit vector: the index rejects zero norms.
        vec[0] = 1.0
        return vec
    return [v / length for v in vec]


# --- scripted extractor ---------------------------------------------------


class _SchemaFiller:
    """Build one minimal valid instance of a JSON Schema, carrying `text`."""

    def __init__(self, root: dict[str, Any], text: str) -> None:
        self._defs = root.get("$defs", {}) | root.get("definitions", {})
        self._text = text
        self._next_page_id = 100

    def _resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        seen = 0
        while "$ref" in schema and seen < 10:
            name = str(schema["$ref"]).rsplit("/", 1)[-1]
            schema = self._defs.get(name, {})
            seen += 1
        for key in ("anyOf", "oneOf", "allOf"):
            if key in schema:
                branches = [b for b in schema[key] if b.get("type") != "null"]
                if branches:
                    merged = {k: v for k, v in schema.items() if k != key}
                    return merged | self._resolve(dict(branches[0]))
        return schema

    def build(self, schema: dict[str, Any], name: str = "") -> Any:
        schema = self._resolve(dict(schema))
        if "enum" in schema and schema["enum"]:
            return schema["enum"][0]
        if "const" in schema:
            return schema["const"]
        type_ = schema.get("type")
        if isinstance(type_, list):
            type_ = next((t for t in type_ if t != "null"), "string")
        if type_ == "object" or (type_ is None and "properties" in schema):
            return self._build_object(schema)
        if type_ == "array":
            item = schema.get("items")
            return [self.build(item, name)] if isinstance(item, dict) else []
        if type_ == "integer":
            return self._next_int(name)
        if type_ == "number":
            return float(self._next_int(name))
        if type_ == "boolean":
            return False
        return self._string_for(name, schema)

    def _build_object(self, schema: dict[str, Any]) -> dict[str, Any]:
        props: dict[str, Any] = schema.get("properties", {})
        required = set(schema.get("required", list(props)))
        # Required fields (identity + page_id) plus one body field to carry the
        # actual text — an item with only identity fields is an empty card.
        out: dict[str, Any] = {}
        for name, sub in props.items():
            if name not in required and name != "page_id" and name not in _BODY_FIELDS:
                continue
            out[name] = self.build(sub, name)
        return out

    def _next_int(self, name: str) -> int:
        if name == "page_id":
            self._next_page_id += 1
            return self._next_page_id - 1
        return 1

    def _string_for(self, name: str, schema: dict[str, Any]) -> str:
        fmt = str(schema.get("format", ""))
        if fmt in {"date-time", "date"}:
            # Fixed instant: these fixtures must not depend on wall-clock time.
            return "2026-01-01T00:00:00Z" if fmt == "date-time" else "2026-01-01"
        if name in _BODY_FIELDS:
            return self._text
        if name == "ranges":
            # Message indices of the extracted turn, in OpenViking's own
            # notation: "0-10,15" — comma-separated indices and spans, each
            # side parsed with int(). Anything bracket-shaped raises there.
            return "0"
        return self.short_label()

    def short_label(self) -> str:
        """Path-safe stem for the card file OpenViking names after this item."""
        stem = _UNSAFE_IN_NAME.sub("-", self._text).strip("-")[:24]
        return stem or "memory"


def scripted_reply(messages: list[dict[str, Any]]) -> str:
    """Answer whichever of OpenViking's two model calls this is.

    Session compression asks for a prose note and hands us no schema; memory
    extraction embeds the operations JSON Schema in its system prompt.
    """
    system = "\n".join(_content_text(m) for m in messages if m.get("role") == "system")
    match = _SCHEMA_BLOCK.search(system)
    if match is None:
        return _conversation_text(messages)
    return _operations_json(json.loads(match.group(1)), _conversation_text(messages))


def _operations_json(schema: dict[str, Any], text: str) -> str:
    """The scripted extractor's reply: JSON operations for the ReAct loop."""

    filler = _SchemaFiller(schema, text)
    props: dict[str, Any] = schema.get("properties", {})
    ops: dict[str, Any] = {"delete_uris": []}
    if "links" in props:
        ops["links"] = []
    target = _target_memory_type(props, skip=set(ops))
    if target is not None:
        # One item, in one memory type. Enough to prove the write→index→search
        # path; picking a *good* type is the model's job.
        ops[target] = filler.build(props[target], target)
    return json.dumps(ops, ensure_ascii=False)


def _target_memory_type(props: dict[str, Any], skip: set[str]) -> str | None:
    """Which memory type the stand-in writes into — deterministically.

    Taking "whichever key came first" made the fixture depend on the order
    OpenViking happens to build its schema in, which is not stable across
    processes: locally it yielded `preferences`, in CI `events`, and an events
    item needs fields (message ranges) a stand-in has no honest value for.
    """
    candidates = [name for name in props if name not in skip]
    for preferred in ("preferences", "entities", "profile"):
        if preferred in candidates:
            return preferred
    return sorted(candidates)[0] if candidates else None


def _content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def _conversation_text(messages: list[dict[str, Any]]) -> str:
    """The turn under extraction, as the fake memory card's body.

    Both callers ship the transcript in a non-system message: the compressor
    wraps it in ``<session_content>``, the extractor lists it under
    ``## Conversation History`` as ``[i][role][name]: text`` lines. Anything
    else in the prompt (tool results, instructions) is not the conversation.
    """
    for message in messages:
        if message.get("role") == "system":
            continue
        content = _content_text(message)
        wrapped = _SESSION_CONTENT.search(content)
        if wrapped:
            return _condense(wrapped.group(1))
        spoken = [
            m.group(1) for m in map(_HISTORY_LINE.match, content.splitlines()) if m
        ]
        if spoken:
            return _condense(" ".join(spoken))
    return "（无内容）"


def _condense(text: str) -> str:
    return " ".join(text.split())[:1500] or "（无内容）"


# --- server ---------------------------------------------------------------


def build_app(*, calls: list[dict[str, Any]] | None = None) -> FastAPI:
    """OpenAI-compatible app. `calls` collects requests for assertions."""
    app = FastAPI()
    log = calls if calls is not None else []

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        body = await request.json()
        log.append({"kind": "embeddings", "body": body})
        raw = body.get("input")
        inputs = raw if isinstance(raw, list) else [raw]
        dimension = int(body.get("dimensions") or 2048)
        return {
            "object": "list",
            "model": body.get("model", "fake-embedding"),
            "data": [
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": fake_embedding(_as_text(item), dimension),
                }
                for i, item in enumerate(inputs)
            ],
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        body = await request.json()
        log.append({"kind": "chat", "body": body})
        content = scripted_reply(body.get("messages") or [])
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "fake-chat"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        return {"object": "list", "data": []}

    return app


def _as_text(item: Any) -> str:
    # OpenAI allows token-id arrays as input; the stand-in only needs a key.
    return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)


@contextmanager
def fake_model_server(port: int = 0) -> Iterator[dict[str, Any]]:
    """Serve the fake endpoint on 127.0.0.1 for the duration of the block.

    Yields ``{"base_url": ..., "calls": [...]}``. Port 0 picks a free one, so
    concurrent xdist workers don't collide.
    """
    import uvicorn

    calls: list[dict[str, Any]] = []
    config = uvicorn.Config(
        build_app(calls=calls), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("fake model endpoint died during startup")
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("fake model endpoint did not start in time")
    bound = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield {"base_url": f"http://127.0.0.1:{bound}/v1", "calls": calls}
    finally:
        server.should_exit = True
        thread.join(timeout=30)


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
