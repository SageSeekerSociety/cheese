"""OpenViking-backed MemoryStore (spec §8.4, §15 Q9 — real layered memory).

Deployment shape (verified against openviking 0.4.7 source):
- Embedded mode: ``AsyncOpenViking`` runs fully in-process on local storage
  (bundled Rust AGFS + local vector index under ``settings.openviking_data_dir``).
  No server, no cloud dependency.
- Model calls: OpenViking needs an OpenAI-compatible chat endpoint (memory
  extraction / summaries) and embedding endpoint (vectors). Configured via a
  generated ``ov.conf`` pointing at ``settings.openviking_*``.

Scope mapping — every CheeseX memory scope gets its own OpenViking user space,
so each project / person / skill pack has an isolated ``viking://user/{uid}``
tree with the full built-in taxonomy:

    scope=project, id=<uuid>   → viking://user/project-<uuid>/memories/...
    scope=user,    id=<handle> → viking://user/user-<handle>/memories/...
    scope=skill,   id=<name>   → viking://user/skill-<name>/memories/...

Inside each space OpenViking's extractor files memories into its taxonomy
(preferences/entities/events/tools/... — a superset of omem's
user/feedback/project/reference types; see settings.openviking_memory_types
for the mapping). Placement and wording are decided by OpenViking's LLM
extraction, never by platform-side text parsing (规则4).

Layers: ``recall`` injects L0 abstracts only; full text stays behind
``search`` (semantic find → L0 abstract + URI) and ``read_memory`` (L2).
"""

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.domain.memory.models import MemoryLayer, MemoryScope

logger = logging.getLogger(__name__)

_ACCOUNT = "cheesex"
# Charset accepted by OpenViking identifiers (openviking.core.identifiers).
_SAFE_ID = re.compile(r"[^a-zA-Z0-9_.@-]")
# OpenViking appends a machine-readable metadata comment to each memory card.
_MEMORY_FIELDS_RE = re.compile(r"<!--\s*MEMORY_FIELDS\b.*?-->", re.DOTALL)
# The core layer gets a scope space of its own rather than a marker inside the
# shared one. OpenViking's extractor decides placement and wording for every
# card it files, so there is no field here we could set and later trust — but
# "which space is this in" is ours. A separate space is also the only way to
# read the whole core layer back cheaply, which is what injecting it every turn
# requires.
_CORE_SPACE_SUFFIX = "-core"


def scope_user_id(
    scope: MemoryScope, scope_id: str, layer: MemoryLayer = MemoryLayer.fact
) -> str:
    """Deterministic OpenViking user id for a CheeseX memory scope + layer.

    Invalid chars are replaced with ``_``; a short hash keeps sanitized ids
    collision-free (two distinct handles must never share a memory space).
    """
    raw = scope_id.strip()
    safe = _SAFE_ID.sub("_", raw)
    if safe != raw or not safe:
        import hashlib

        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        safe = f"{safe.strip('_') or 'x'}-{digest}"
    suffix = _CORE_SPACE_SUFFIX if layer is MemoryLayer.core else ""
    return f"{scope.value}-{safe}{suffix}"


def memories_uri(
    scope: MemoryScope, scope_id: str, layer: MemoryLayer = MemoryLayer.fact
) -> str:
    return f"viking://user/{scope_user_id(scope, scope_id, layer)}/memories"


@dataclass
class MemoryHit:
    """One search hit: L0 abstract + address for on-demand L2 read."""

    uri: str
    abstract: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "abstract": self.abstract, "score": self.score}


class _OpenVikingRuntime:
    """Process-wide embedded OpenViking client + per-scope request contexts.

    ``AsyncOpenViking`` is a singleton in the openviking package; this wrapper
    owns its lifecycle and hands out per-scope ``RequestContext`` objects the
    same way the OpenViking HTTP server multiplexes users over one service.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._lock: asyncio.Lock | None = None
        self._known_users: set[str] = set()

    def _write_conf(self) -> str:
        """Materialize ov.conf from app settings (idempotent, secrets stay local)."""
        import json
        from pathlib import Path

        data_dir = Path(settings.openviking_data_dir).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        llm_key = settings.openviking_llm_api_key or settings.anthropic_auth_token
        emb_key = settings.openviking_embedding_api_key or settings.anthropic_auth_token
        conf = {
            "default_account": _ACCOUNT,
            "default_user": "platform",
            "storage": {"workspace": str(data_dir / "data")},
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "api_base": settings.openviking_embedding_api_base,
                    "api_key": emb_key,
                    "model": settings.openviking_embedding_model,
                    "dimension": settings.openviking_embedding_dimension,
                    "input": "text",
                    "encoding_format": "float",
                }
            },
            "vlm": {
                "provider": "openai",
                "api_base": settings.openviking_llm_api_base,
                "api_key": llm_key,
                "model": settings.openviking_llm_model,
                # Zhipu GLM: don't burn tokens on thinking for extraction calls.
                "extra_request_body": {"thinking": {"type": "disabled"}},
            },
        }
        conf_path = data_dir / "ov.conf"
        conf_path.write_text(json.dumps(conf, ensure_ascii=False, indent=2))
        return str(conf_path)

    async def client(self) -> Any:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._client is None:
                import os

                conf_path = self._write_conf()
                os.environ["OPENVIKING_CONFIG_FILE"] = conf_path
                # Force-load OUR config into the singleton. Merely setting the
                # env var is not enough: any earlier openviking import may have
                # initialized the singleton from a stray ~/.openviking/ov.conf
                # (module-level get_logger() resolves config), which would
                # silently swap in wrong storage/embedding settings.
                from openviking_cli.utils.config.open_viking_config import (
                    OpenVikingConfigSingleton,
                )

                OpenVikingConfigSingleton.initialize(config_path=conf_path)
                from openviking import AsyncOpenViking

                client = AsyncOpenViking(path=None)
                await client.initialize()
                self._client = client
                logger.info(
                    "openviking: embedded client initialized (data=%s)",
                    settings.openviking_data_dir,
                )
        return self._client

    async def ctx_for_uid(self, uid: str) -> Any:
        """RequestContext for an OpenViking user space; creates it on first use."""
        client = await self.client()
        from openviking.server.identity import RequestContext, Role
        from openviking_cli.session.user_id import UserIdentifier

        # Role is a str subclass; its class attrs are plain literals, so wrap
        # explicitly to satisfy the constructor's declared type.
        ctx = RequestContext(user=UserIdentifier(_ACCOUNT, uid), role=Role(Role.USER))
        if uid not in self._known_users:
            # Same bootstrap the OpenViking server does for a new user: create
            # the memories/... taxonomy roots (idempotent).
            await client._service.initialize_user_directories(ctx)  # noqa: SLF001
            self._known_users.add(uid)
        return ctx

    async def ctx_for(
        self, scope: MemoryScope, scope_id: str, layer: MemoryLayer = MemoryLayer.fact
    ) -> Any:
        return await self.ctx_for_uid(scope_user_id(scope, scope_id, layer))

    async def service(self) -> Any:
        client = await self.client()
        return client._service  # noqa: SLF001

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._known_users.clear()


_runtime = _OpenVikingRuntime()


def get_runtime() -> _OpenVikingRuntime:
    return _runtime


class OpenVikingMemoryStore:
    """MemoryStore on embedded OpenViking. Stateless; safe to construct per call."""

    def __init__(self) -> None:
        self._rt = get_runtime()

    # Extractor anchor files (agent self-identity boilerplate) and agent-SOP
    # subtrees — kept in storage but never injected into prompts.
    _SKIP_FILES = frozenset({"identity.md", "soul.md"})
    _SKIP_DIRS = ("trajectories/", "experiences/")
    # Memory files are compact cards (a few hundred bytes); cap what a single
    # runaway card can occupy in the prompt. This is a length budget, not
    # text interpretation.
    _CARD_CHARS = 600

    # --- MemoryStore protocol ---

    async def recall(
        self, scope: MemoryScope, scope_id: str, limit: int = 50
    ) -> list[str]:
        """Both layers: one line per memory card, newest last.

        Each extracted memory is a compact card file; its content IS the
        atomic memory unit (the omem model), so recall reads the newest cards
        directly. Everything else — session archives, trajectories, resources
        — stays behind on-demand ``search``/``read_memory`` (spec §15 Q9).
        """
        entries: list[tuple[MemoryLayer, dict[str, Any]]] = []
        for layer in (MemoryLayer.core, MemoryLayer.fact):
            entries += [
                (layer, e) for e in await self._memory_files(scope, scope_id, layer)
            ]
        entries.sort(key=lambda pair: str(pair[1].get("modTime", "")))
        return await self._read_entries(scope, scope_id, entries[-limit:])

    async def recall_core(self, scope: MemoryScope, scope_id: str) -> list[str]:
        """The whole core layer of a scope, oldest first — its own space, so
        this is a full listing rather than a search."""
        return await self._cards(scope, scope_id, MemoryLayer.core)

    async def rank_facts(
        self,
        scope: MemoryScope,
        scope_id: str,
        query: str,
        limit: int = 500,
    ) -> list[tuple[float, str]]:
        """Non-core facts scored against the turn's context.

        This is the one place the two backends genuinely differ in kind: here
        the ranking is OpenViking's semantic search, so a fact can be retrieved
        for meaning something related rather than for sharing a word. An empty
        query has nothing to search with, so it degrades to newest-first at
        score 0.0 — the same floor the flat backend has.
        """
        if not query.strip():
            return [
                (0.0, line) for line in reversed(await self.recall(scope, scope_id))
            ]
        hits = await self.search(scope, scope_id, query, limit=limit)
        return [(h.score, h.abstract) for h in hits if h.abstract]

    async def count(self, scope: MemoryScope, scope_id: str) -> int:
        """Memory cards in the scope, both layers, including the ones a turn
        did not retrieve — injection subtracts the two to report what is
        missing from this turn."""
        return len(await self._memory_files(scope, scope_id)) + len(
            await self._memory_files(scope, scope_id, MemoryLayer.core)
        )

    async def remember(
        self,
        scope: MemoryScope,
        scope_id: str,
        content: str,
        *,
        layer: MemoryLayer = MemoryLayer.fact,
    ) -> None:
        """Persist a fact through OpenViking's canonical write path: a one-shot
        session commit. OpenViking's extractor classifies/merges/dedups it into
        the taxonomy (语义分类由 AI 做, 规则4)."""
        service = await self._rt.service()
        ctx = await self._rt.ctx_for(scope, scope_id, layer)
        from openviking.message.part import TextPart

        session_id = f"remember-{uuid.uuid4().hex[:12]}"
        session = await self._session(service, ctx, session_id)
        session.add_message("user", [TextPart(text=f"请记住：{content}")])
        await service.sessions.commit_async(session_id, ctx)

    async def search(
        self, scope: MemoryScope, scope_id: str, query: str, limit: int = 8
    ) -> list[MemoryHit]:
        """Semantic search within one scope's memory tree. Returns L0 hits."""
        service = await self._rt.service()
        ctx = await self._rt.ctx_for(scope, scope_id)
        result = await service.search.find(
            query=query,
            ctx=ctx,
            target_uri=memories_uri(scope, scope_id),
            limit=limit,
        )
        data = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
        hits: list[MemoryHit] = []
        for h in data.get("memories", []):
            hits.append(
                MemoryHit(
                    uri=str(h.get("uri", "")),
                    abstract=" ".join(str(h.get("abstract") or "").split()),
                    score=float(h.get("score") or 0.0),
                )
            )
        return hits

    # --- extra capabilities used by routes / turn hook ---

    async def read_memory(self, scope: MemoryScope, scope_id: str, uri: str) -> str:
        """L2 layer: full text of one memory file (must live in this scope)."""
        self._guard_uri(scope, scope_id, uri)
        service = await self._rt.service()
        ctx = await self._rt.ctx_for(scope, scope_id)
        return await service.fs.read(uri, ctx=ctx)

    async def forget(self, scope: MemoryScope, scope_id: str, uri: str) -> None:
        """人工修剪一条记忆 (routes/memory.py DELETE)."""
        self._guard_uri(scope, scope_id, uri)
        service = await self._rt.service()
        ctx = await self._rt.ctx_for(scope, scope_id)
        await service.fs.rm(uri, ctx=ctx, recursive=False)

    async def list_entries(
        self, scope: MemoryScope, scope_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Flat listing for the memory page: uri + card content + modTime.

        Both layers — the page exists so a human can see and prune what the
        agent remembers, and the core layer is the part that costs a seat on
        every single turn, so hiding it there would hide exactly the entries
        most worth curating.
        """
        entries: list[tuple[MemoryLayer, dict[str, Any]]] = []
        for layer in (MemoryLayer.core, MemoryLayer.fact):
            entries += [
                (layer, e) for e in await self._memory_files(scope, scope_id, layer)
            ]
        entries.sort(key=lambda pair: str(pair[1].get("modTime", "")), reverse=True)
        picked = entries[:limit]
        if not picked:
            return []
        service = await self._rt.service()
        ctxs = {
            layer: await self._rt.ctx_for(scope, scope_id, layer)
            for layer in {layer for layer, _ in picked}
        }
        contents = await asyncio.gather(
            *(
                self._read_card(service, ctxs[layer], str(e.get("uri", "")))
                for layer, e in picked
            )
        )
        return [
            {
                "uri": str(e.get("uri", "")),
                "rel_path": str(e.get("rel_path", "")),
                "abstract": content,
                "mod_time": str(e.get("modTime", "")),
                "layer": layer.value,
            }
            for (layer, e), content in zip(picked, contents, strict=True)
        ]

    async def _cards(
        self, scope: MemoryScope, scope_id: str, layer: MemoryLayer
    ) -> list[str]:
        """Every card of one layer, oldest first."""
        files = await self._memory_files(scope, scope_id, layer)
        files.sort(key=lambda e: str(e.get("modTime", "")))
        return await self._read_entries(scope, scope_id, [(layer, e) for e in files])

    async def _read_entries(
        self,
        scope: MemoryScope,
        scope_id: str,
        entries: list[tuple[MemoryLayer, dict[str, Any]]],
    ) -> list[str]:
        """Card contents for listed files, in the order given; empties dropped."""
        if not entries:
            return []
        service = await self._rt.service()
        ctxs = {
            layer: await self._rt.ctx_for(scope, scope_id, layer)
            for layer in {layer for layer, _ in entries}
        }
        contents = await asyncio.gather(
            *(
                self._read_card(service, ctxs[layer], str(e.get("uri", "")))
                for layer, e in entries
            )
        )
        return [
            f"[{e.get('rel_path', '')}] {content}"
            for (_, e), content in zip(entries, contents, strict=True)
            if content
        ]

    async def _memory_files(
        self,
        scope: MemoryScope,
        scope_id: str,
        layer: MemoryLayer = MemoryLayer.fact,
    ) -> list[dict[str, Any]]:
        """Memory card files of one scope layer (structural filter, no NL parsing)."""
        service = await self._rt.service()
        ctx = await self._rt.ctx_for(scope, scope_id, layer)
        try:
            entries = await service.fs.tree(
                memories_uri(scope, scope_id, layer),
                ctx=ctx,
                output="original",
                show_all_hidden=False,
                node_limit=1000,
                level_limit=6,
            )
        except Exception:  # noqa: BLE001 - missing tree == no memories yet
            return []
        files: list[dict[str, Any]] = []
        for e in entries:
            if not isinstance(e, dict) or e.get("isDir"):
                continue
            rel = str(e.get("rel_path", ""))
            if rel in self._SKIP_FILES or rel.startswith(self._SKIP_DIRS):
                continue
            files.append(e)
        return files

    async def _read_card(self, service: Any, ctx: Any, uri: str) -> str:
        """Whitespace-condensed card content, capped at the per-card budget."""
        try:
            text = await service.fs.read(uri, ctx=ctx)
        except Exception:  # noqa: BLE001 - a racing prune must not kill recall
            return ""
        # Strip OpenViking's structured metadata trailer (its own machine
        # token, not natural language) — prompts get the human part only.
        text = _MEMORY_FIELDS_RE.sub("", str(text))
        condensed = " ".join(text.split())
        if len(condensed) > self._CARD_CHARS:
            condensed = condensed[: self._CARD_CHARS] + "…"
        return condensed

    async def ingest_turn(
        self,
        scope: MemoryScope,
        scope_id: str,
        conversation_key: str,
        exchanges: list[tuple[str, str]],
    ) -> None:
        """会话结束自动提取 (spec §8.4 知识沉淀是副产品): append this turn's
        (role, text) exchanges to the scope's rolling OpenViking session and
        commit — extraction runs in OpenViking's background task."""
        service = await self._rt.service()
        ctx = await self._rt.ctx_for(scope, scope_id)
        from openviking.message.part import TextPart

        session_id = f"turns-{_SAFE_ID.sub('_', conversation_key)}"
        session = await self._session(service, ctx, session_id)
        added = 0
        for role, text in exchanges:
            if text.strip():
                session.add_message(role, [TextPart(text=text)])
                added += 1
        if not added:
            return
        t0 = time.monotonic()
        await service.sessions.commit_async(session_id, ctx)
        logger.info(
            "openviking: committed %d message(s) of %s for extraction (%.0fms)",
            added,
            session_id,
            (time.monotonic() - t0) * 1000,
        )

    # --- helpers ---

    @staticmethod
    async def _session(service: Any, ctx: Any, session_id: str) -> Any:
        """Get-or-create an OpenViking session carrying our extraction policy.

        memory_policy is a session-level property fixed at creation; it caps
        which taxonomy types the extractor may write (settings mapping of the
        omem typology)."""
        from openviking_cli.exceptions import AlreadyExistsError

        try:
            return await service.sessions.create(
                ctx,
                session_id,
                memory_policy={"memory_types": list(settings.openviking_memory_types)},
            )
        except AlreadyExistsError:
            return await service.sessions.get(session_id, ctx)

    @staticmethod
    def _guard_uri(scope: MemoryScope, scope_id: str, uri: str) -> None:
        prefix = memories_uri(scope, scope_id)
        if not uri.startswith(prefix + "/"):
            raise ValueError(f"uri outside scope memory tree: {uri}")


_URI_RE = re.compile(
    r"^viking://user/(?P<uid>(?:project|user|skill)-[a-zA-Z0-9_.@-]+)/memories/."
)


async def forget_uri(uri: str) -> None:
    """Delete one memory file addressed by its viking:// URI (memory page).

    The URI must sit inside a CheeseX-managed scope space
    (viking://user/{scope}-{id}/memories/...), so this can't touch sessions,
    resources, or foreign user spaces."""
    m = _URI_RE.match(uri)
    if m is None:
        raise ValueError(f"not a cheesex memory uri: {uri}")
    rt = get_runtime()
    service = await rt.service()
    ctx = await rt.ctx_for_uid(m.group("uid"))
    await service.fs.rm(uri, ctx=ctx, recursive=False)
