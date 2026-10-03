"""The remote servers a project declares: its committed `.mcp.json`, and its
teammates' types.

`.mcp.json` is read from the default branch through the forge, never from a
room's checkout: the agent can edit its checkout, and a server URL taken from
there would let it point a connected name at a host of its choosing and receive
the project's token. Only committed configuration decides where a token may be
sent — the project's own, or an agent type's, which ships with the platform.

A type's server whose name the project's `.mcp.json` also uses is left out:
the project's committed configuration decides which host a name reaches, and a
connection is the project's, kept by name.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import background
from app.core.errors import GatewayUnavailableError
from app.core.sentences import say
from app.domain.textfile import MAX_TEXT_BYTES, decode_text

logger = logging.getLogger(__name__)

#: `${VAR}` and `${VAR:-default}`, as Claude Code expands them in `.mcp.json`.
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

#: How long one read of `.mcp.json` is taken as current. An older answer is
#: still given, and read again behind it (`read`).
_FRESH_S = 60
#: A refresh still unanswered after this long is taken as lost.
_REFRESH_GIVE_UP_S = 120


@dataclass
class _Entry:
    read_at: float
    declared: Declared
    refresh_started: float | None = None


_cache: dict[uuid.UUID, _Entry] = {}


@dataclass(frozen=True)
class RemoteServer:
    name: str
    #: "http" (Streamable HTTP) or "sse" (the 2024-11-05 HTTP+SSE transport)
    transport: str
    url: str
    #: Header templates; a server that declares any is authorized by them
    #: rather than by OAuth.
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def uses_oauth(self) -> bool:
        return not self.headers

    def variables(self) -> list[str]:
        """The `${VAR}` names without a default, in first-use order."""
        names: list[str] = []
        for text in (self.url, *self.headers.values()):
            for match in _VARIABLE.finditer(text):
                if match.group(2) is None and match.group(1) not in names:
                    names.append(match.group(1))
        return names

    def all_variables(self) -> list[str]:
        names: list[str] = []
        for text in (self.url, *self.headers.values()):
            for match in _VARIABLE.finditer(text):
                if match.group(1) not in names:
                    names.append(match.group(1))
        return names

    def expanded(self, values: dict[str, str]) -> tuple[str, dict[str, str]]:
        """The URL and headers with every variable substituted. Raises KeyError
        naming the first variable that has neither a value nor a default."""
        return expand(self.url, values), {
            name: expand(value, values) for name, value in self.headers.items()
        }


@dataclass(frozen=True)
class Declared:
    servers: tuple[RemoteServer, ...] = ()
    #: Every name `.mcp.json` uses, stdio entries included.
    names: tuple[str, ...] = ()
    #: Why nothing could be read, when that is the answer: "missing" (no
    #: `.mcp.json` on the default branch), "invalid", or "unreadable".
    problem: str | None = None

    def get(self, name: str) -> RemoteServer | None:
        return next((s for s in self.servers if s.name == name), None)


def expand(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        name, default = match.group(1), match.group(2)
        if name in values:
            return values[name]
        if default is not None:
            return default
        raise KeyError(name)

    return _VARIABLE.sub(replace, text)


def canonical_resource(url: str) -> str:
    """The canonical URI of an MCP server (spec 2025-11-25 §Resource Parameter):
    lowercase scheme and host, no fragment."""
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )


def parse(text: str) -> Declared:
    """The remote servers in a `.mcp.json` document. stdio entries (a `command`
    and no `url`) are the executor's and are not listed here."""
    try:
        document = json.loads(text)
    except ValueError:
        return Declared(problem="invalid")
    entries = document.get("mcpServers") if isinstance(document, dict) else None
    if not isinstance(entries, dict):
        return Declared(problem="invalid")
    servers = [
        remote(str(name), spec)
        for name, spec in entries.items()
        if isinstance(spec, dict) and isinstance(spec.get("url"), str)
    ]
    return Declared(servers=tuple(servers), names=tuple(str(n) for n in entries))


def remote(name: str, spec: dict) -> RemoteServer:
    """One remote entry, as `.mcp.json` or an agent type writes it."""
    headers = spec.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    return RemoteServer(
        name=name,
        transport="sse" if spec.get("type") == "sse" else "http",
        url=spec["url"],
        headers={str(k): str(v) for k, v in headers.items()},
    )


def with_types(found: Declared, types: Iterable) -> Declared:
    """The project's servers and its teammates' types' remote ones, where the
    types are ``AgentTypeDef``s. A name the project uses stays the project's."""
    servers, taken = list(found.servers), set(found.names)
    for agent_type in types:
        for name, spec in agent_type.inline_servers().items():
            if name in taken or not isinstance(spec.get("url"), str):
                continue
            taken.add(name)
            servers.append(remote(name, spec))
    return replace(found, servers=tuple(servers))


async def read(
    db: AsyncSession, project_id: uuid.UUID, *, fresh: bool = False
) -> Declared:
    """The remote servers the project's default branch declares.

    Answered from the last read whenever there is one: a read older than
    `_FRESH_S` is refreshed in the background, and the caller does not wait
    for it. Only a project never read before, or `fresh` (the settings page,
    where a member has just changed something), waits for the forge."""
    now = time.monotonic()
    entry = _cache.get(project_id)
    if fresh or entry is None:
        try:
            declared = await _fetch(db, project_id)
        except Exception:  # noqa: BLE001 — a forge outage must not fail a session
            logger.warning("remote_mcp: .mcp.json unreadable project=%s", project_id)
            # The last answer stands while the forge is down. A session's
            # servers are part of what it was started with, so answering "none"
            # for the length of an outage would relaunch every idle session twice.
            return entry.declared if entry else Declared(problem="unreadable")
        _cache[project_id] = _Entry(now, declared)
        return declared
    if now - entry.read_at >= _FRESH_S and not _refreshing(entry, now):
        entry.refresh_started = now
        background.spawn(_refresh(_engine_of(db), project_id), name="read .mcp.json")
    return entry.declared


def _refreshing(entry: _Entry, now: float) -> bool:
    # A refresh that never reported back (its event loop is gone) stops
    # counting once it is older than any read could take.
    started = entry.refresh_started
    return started is not None and now - started < _REFRESH_GIVE_UP_S


def _engine_of(db: AsyncSession) -> AsyncEngine:
    bind = db.bind
    return bind if isinstance(bind, AsyncEngine) else bind.engine


async def _refresh(engine: AsyncEngine, project_id: uuid.UUID) -> None:
    # Its own session on the caller's engine: the caller's session is the
    # turn's, and is closed or committed long before the forge answers.
    started = time.monotonic()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            declared = await _fetch(session, project_id)
    except Exception:  # noqa: BLE001 — the last answer stands, as in `read`
        logger.warning("remote_mcp: .mcp.json unreadable project=%s", project_id)
        entry = _cache.get(project_id)
        if entry is not None:
            # Asked again after another window, not on every turn of an outage.
            entry.read_at, entry.refresh_started = time.monotonic(), None
        return
    entry = _cache.get(project_id)
    if entry is None or entry.read_at <= started:
        # A read the settings page made meanwhile is newer than this one.
        _cache[project_id] = _Entry(time.monotonic(), declared)


async def _fetch(db: AsyncSession, project_id: uuid.UUID) -> Declared:
    """One read of `.mcp.json` on the default branch: the contents endpoint,
    which GitHub and Forgejo both answer from the default branch when no ref
    is named. A directory, symlink or submodule there is not a file."""
    from app.domain.project import forge

    found = await forge.repository_data(project_id, db, "/contents/.mcp.json")
    if not isinstance(found, dict) or found.get("type") != "file":
        return Declared(problem="missing")
    if (found.get("size") or 0) > MAX_TEXT_BYTES:
        return Declared(problem="invalid")
    if found.get("encoding") != "base64":
        raise GatewayUnavailableError(say("forgeNoFileContent"))
    text = decode_text(base64.b64decode(found.get("content") or ""))
    return parse(text) if text is not None else Declared(problem="invalid")
