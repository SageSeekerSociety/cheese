"""What a project's committed `.mcp.json` declares about remote servers.

Read from the default branch through the forge, never from a room's checkout:
the agent can edit its checkout, and a server URL taken from there would let it
point a connected name at a host of its choosing and receive the project's
token. Only committed configuration decides where a token may be sent.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError

logger = logging.getLogger(__name__)

#: `${VAR}` and `${VAR:-default}`, as Claude Code expands them in `.mcp.json`.
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

#: How long one read of `.mcp.json` answers for a project. A session start and
#: the prompt of the same turn ask within the same second; the settings page
#: always reads fresh.
_CACHE_S = 60
_cache: dict[uuid.UUID, tuple[float, Declared]] = {}


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
    servers = []
    for name, spec in entries.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("url"), str):
            continue
        headers = spec.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        servers.append(
            RemoteServer(
                name=str(name),
                transport="sse" if spec.get("type") == "sse" else "http",
                url=spec["url"],
                headers={str(k): str(v) for k, v in headers.items()},
            )
        )
    return Declared(servers=tuple(servers))


async def read(
    db: AsyncSession, project_id: uuid.UUID, *, fresh: bool = False
) -> Declared:
    """The remote servers the project's default branch declares."""
    now = time.monotonic()
    cached = _cache.get(project_id)
    if not fresh and cached and now - cached[0] < _CACHE_S:
        return cached[1]
    from app.domain.repository.forge_files import ProjectFiles

    try:
        read = await ProjectFiles(db, project_id, None).text(".mcp.json", "committed")
    except NotFoundError:
        declared = Declared(problem="missing")
    except Exception:  # noqa: BLE001 — a forge outage must not fail a session
        logger.warning("remote_mcp: .mcp.json unreadable project=%s", project_id)
        return Declared(problem="unreadable")
    else:
        content = read.get("content")
        declared = (
            parse(content) if content is not None else Declared(problem="invalid")
        )
    _cache[project_id] = (now, declared)
    return declared


def forget(project_id: uuid.UUID) -> None:
    _cache.pop(project_id, None)
