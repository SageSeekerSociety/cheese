"""The MCP servers a project declares: its committed `.mcp.json`, its
settings, and its teammates' types — remote ones, which the platform calls, and
local ones, a command each, which run on the room's machine.

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
import shlex
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import background
from app.core.errors import GatewayUnavailableError
from app.core.sentences import say
from app.domain.project import forge
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
class LocalServer:
    """A server that is a command, run on the room's machine. Read here to
    list it and to know which project secrets it may be handed: what the
    machine runs is its own checkout's definition, or — for one added in
    settings or by a type — the definition the session hands it."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    def line(self) -> str:
        """The command as one line, as a person would type it."""
        return shlex.join([self.command, *self.args])

    def _texts(self) -> list[str]:
        return [self.command, *self.args, *self.env.values()]

    def variables(self) -> list[str]:
        """The `${VAR}` names without a default, in first-use order."""
        return _names(self._texts(), defaulted=False)

    def all_variables(self) -> list[str]:
        return _names(self._texts(), defaulted=True)


def _names(texts: Iterable[str], *, defaulted: bool) -> list[str]:
    names: list[str] = []
    for text in texts:
        for match in _VARIABLE.finditer(text):
            if (defaulted or match.group(2) is None) and match.group(1) not in names:
                names.append(match.group(1))
    return names


@dataclass(frozen=True)
class Declared:
    servers: tuple[RemoteServer, ...] = ()
    #: The local servers: the repository's, those added in settings, and then
    #: its teammates' types'.
    local: tuple[LocalServer, ...] = ()
    #: For each local server a type declares, the types that declare it.
    local_types: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Every name `.mcp.json` uses, stdio entries included.
    names: tuple[str, ...] = ()
    #: The servers added in project settings that are in `servers` or `local`.
    added: tuple[str, ...] = ()
    #: Those added in settings whose name `.mcp.json` also uses: the
    #: repository's stands, and these are left out of `servers`.
    shadowed: tuple[str, ...] = ()
    #: Why nothing could be read, when that is the answer: "missing" (no
    #: `.mcp.json` on the default branch), "invalid", or "unreadable".
    problem: str | None = None

    def get(self, name: str) -> RemoteServer | None:
        return next((s for s in self.servers if s.name == name), None)

    def get_local(self, name: str) -> LocalServer | None:
        return next((s for s in self.local if s.name == name), None)


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
    """The servers in a `.mcp.json` document: remote ones (a `url`), and local
    ones (a `command`), which the executor runs."""
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
    local = [
        found
        for name, spec in entries.items()
        if (found := local_server(str(name), spec)) is not None
    ]
    return Declared(
        servers=tuple(servers),
        local=tuple(local),
        names=tuple(str(n) for n in entries),
    )


def local_server(name: str, spec: object) -> LocalServer | None:
    """One local entry, as `.mcp.json` or an agent type writes it; None for
    anything that is not one."""
    if not isinstance(spec, dict) or isinstance(spec.get("url"), str):
        return None
    command = spec.get("command")
    if not isinstance(command, str) or not command:
        return None
    args = spec.get("args") or []
    env = spec.get("env") or {}
    return LocalServer(
        name=name,
        command=command,
        args=tuple(str(a) for a in args) if isinstance(args, list) else (),
        env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
    )


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


def settings_variable(name: str) -> str:
    """The project secret that holds the header value of a server added in
    settings: a `${VAR}` like any other, so the call path expands it."""
    return "CHEESE_MCP_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def settings_server(name: str, url: str, header_name: str | None) -> RemoteServer:
    return RemoteServer(
        name=name,
        transport="http",
        url=url,
        headers={header_name: "${" + settings_variable(name) + "}"}
        if header_name
        else {},
    )


def with_settings(
    found: Declared, added: Iterable[RemoteServer | LocalServer]
) -> Declared:
    """The repository's servers and those added in project settings, remote
    and local. A name the repository's `.mcp.json` uses stays the
    repository's: the one added in settings is shadowed, and said to be."""
    servers, local = list(found.servers), list(found.local)
    kept, shadowed = [], []
    for server in added:
        if server.name in found.names:
            shadowed.append(server.name)
            continue
        if isinstance(server, LocalServer):
            local.append(server)
        else:
            servers.append(server)
        kept.append(server.name)
    return replace(
        found,
        servers=tuple(servers),
        local=tuple(local),
        added=tuple(kept),
        shadowed=tuple(shadowed),
    )


def with_types(found: Declared, types: Iterable) -> Declared:
    """The project's servers and its teammates' types' own, where the types are
    ``AgentTypeDef``s. A name the project uses, in its `.mcp.json` or its
    settings, stays the project's. Several types may declare the same local
    server; it is listed once, with every type that declares it."""
    servers, taken = list(found.servers), set(found.names) | set(found.added)
    local, local_types = list(found.local), dict(found.local_types)
    for agent_type in types:
        for name, spec in agent_type.inline_servers().items():
            if name in local_types:
                local_types[name] = (*local_types[name], agent_type.name)
                continue
            if name in taken:
                continue
            if isinstance(spec.get("url"), str):
                taken.add(name)
                servers.append(remote(name, spec))
            elif (own := local_server(name, spec)) is not None:
                taken.add(name)
                local.append(own)
                local_types[name] = (agent_type.name,)
    return replace(
        found, servers=tuple(servers), local=tuple(local), local_types=local_types
    )


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
        except Exception:  # a forge outage must not fail a session
            logger.warning("remote_mcp: .mcp.json unreadable project=%s", project_id)
            # The last answer stands while the forge is down. A session's
            # servers are part of what it was started with, so answering "none"
            # for the length of an outage would relaunch every idle session twice.
            if entry is None:
                # 从没读成过的项目也记这一笔：不记的话每个调用者每次都重打一次
                # 必败的 forge 调用、重告一次警（一轮两个调用者 = 每轮两条告警）。
                # 记成与成功读同一条新鲜度规矩（60 秒后后台再试），forge 恢复后
                # 自己缓过来 —— 失败缓存的上限就是 _FRESH_S 那一分钟。
                _cache[project_id] = _Entry(now, Declared(problem="unreadable"))
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
    except Exception:  # the last answer stands, as in `read`
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

    found = await forge.repository_data(project_id, db, "/contents/.mcp.json")
    if not isinstance(found, dict) or found.get("type") != "file":
        return Declared(problem="missing")
    if (found.get("size") or 0) > MAX_TEXT_BYTES:
        return Declared(problem="invalid")
    if found.get("encoding") != "base64":
        raise GatewayUnavailableError(say("forgeNoFileContent"))
    text = decode_text(base64.b64decode(found.get("content") or ""))
    return parse(text) if text is not None else Declared(problem="invalid")


async def last_author(db: AsyncSession, project_id: uuid.UUID) -> str | None:
    """Who last changed `.mcp.json` on the default branch, as the repository
    names them (a commit's author, not a member's handle); None when the forge
    does not say."""
    try:
        found = await forge.repository_data(
            project_id, db, "/commits?path=.mcp.json&limit=1&per_page=1"
        )
    except Exception:  # a name to show is not worth a refusal
        return None
    if not isinstance(found, list) or not found or not isinstance(found[0], dict):
        return None
    author = (found[0].get("commit") or {}).get("author") or {}
    name = author.get("name") if isinstance(author, dict) else None
    return str(name)[:128] if name else None
