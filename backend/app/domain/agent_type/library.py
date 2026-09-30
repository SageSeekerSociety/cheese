"""Built-in starting configurations for new project agents.

Presets ship as Markdown files in ``presets/``, written as Claude Code agent
files are. Creation copies a type's body and skills onto an agent. Its MCP
servers are never copied: a session reads them off the agent's type when it
starts (`agent_instance.services.type_of_seat`), so an agent cannot hold a set
of its own.

一个类型说的是**角色**：人设、技能、外部工具。模型、骨架、思考深度都不在其中
——模型绑在活上（结论 3），骨架是部署的开发者选项（结论 28）。
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

#: What an inline server may be, as `.mcp.json` spells it. A room reaches a
#: stdio server on its machine and an http or sse one through the platform
#: (`app.domain.remote_mcp`); nothing in a room speaks the websocket transport.
_TRANSPORTS = ("stdio", "http", "sse")


@dataclass(frozen=True)
class AgentTypeDef:
    """One built-in starting configuration."""

    name: str
    title: str
    description: str
    # The markdown body = the system prompt this agent runs under.
    body: str
    # Skills loaded at start.
    skills: list[str] = field(default_factory=list)
    # Claude Code's subagent `mcpServers`, in its shape: each entry is either
    # the name of a server the session already has (the project's), or
    # ``{name: definition}`` with a definition as `.mcp.json` writes one. An
    # inline server is this type's alone; teammates of other types never get it.
    mcp_servers: list[str | dict[str, dict]] = field(default_factory=list)

    def inline_servers(self) -> dict[str, dict]:
        """This type's own servers, by name."""
        return {
            name: spec
            for entry in self.mcp_servers
            if isinstance(entry, dict)
            for name, spec in entry.items()
        }


_PRESET_DIR = Path(__file__).resolve().parent / "presets"


def parse_type_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Split a type file into (frontmatter, body).

    The frontmatter is the YAML between two ``---`` fences, as in a Claude Code
    agent file. Text without a leading fence is all body.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    body_start = len(lines)
    end = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end, body_start = i, i + 1
            break
    meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(meta, dict):
        raise ValueError("An agent type's frontmatter must be a mapping")
    return meta, "\n".join(lines[body_start:]).strip()


def parse_list_value(raw: str | list | None) -> list[str]:
    """A list-valued frontmatter field: a YAML list, or one comma-separated
    string as Claude Code accepts for ``tools``. Blanks are dropped."""
    parts = raw.split(",") if isinstance(raw, str) else raw or []
    return [str(part).strip() for part in parts if str(part).strip()]


def parse_mcp_servers(raw: Any, *, type_name: str) -> list[str | dict[str, dict]]:
    """``mcpServers`` as Claude Code reads it from an agent file: a list of
    server names and single-key ``{name: definition}`` mappings. A definition
    this platform cannot run is refused when the library loads, not when a
    session first reaches for it."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{type_name}: mcpServers must be a list")
    entries: list[str | dict[str, dict]] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            entries.append(entry.strip())
            continue
        if not (isinstance(entry, dict) and len(entry) == 1):
            raise ValueError(
                f"{type_name}: an mcpServers entry is a name or {{name: definition}}"
            )
        ((name, spec),) = entry.items()
        if name == "native":
            # The room's file operations travel as an MCP server of this name.
            raise ValueError(f"{type_name}: the MCP server name native is reserved")
        if not isinstance(spec, dict):
            raise ValueError(f"{type_name}: MCP server {name} has no definition")
        transport = spec.get("type") or ("http" if "url" in spec else "stdio")
        if transport not in _TRANSPORTS:
            raise ValueError(
                f"{type_name}: MCP server {name} uses {transport}; "
                f"a room reaches {', '.join(_TRANSPORTS)}"
            )
        if transport == "stdio" and not isinstance(spec.get("command"), str):
            raise ValueError(f"{type_name}: stdio MCP server {name} has no command")
        if transport != "stdio" and not isinstance(spec.get("url"), str):
            raise ValueError(f"{type_name}: MCP server {name} has no url")
        entries.append({str(name): dict(spec)})
    return entries


def load_type_library(directory: Path) -> dict[str, AgentTypeDef]:
    """Load every ``*.md`` type file in *directory*, keyed by name.

    The frontmatter ``name`` wins; the filename stem is the fallback. A file
    with an empty body defines no agent and is skipped.

    One server name means one server across the library: a project connects a
    remote server once, by name, for every teammate that declares it.
    """
    types: dict[str, AgentTypeDef] = {}
    if not directory.is_dir():
        return types
    declared: dict[str, tuple[str, dict]] = {}
    for path in sorted(directory.glob("*.md")):
        meta, body = parse_type_markdown(path.read_text(encoding="utf-8"))
        if not body:
            continue
        name = str(meta.get("name") or path.stem)
        definition = AgentTypeDef(
            name=name,
            title=str(meta.get("title", name)),
            description=str(meta.get("description", "")),
            body=body,
            skills=parse_list_value(meta.get("skills")),
            mcp_servers=parse_mcp_servers(meta.get("mcpServers"), type_name=name),
        )
        for server, spec in definition.inline_servers().items():
            first = declared.setdefault(server, (name, spec))
            if first[1] != spec:
                raise ValueError(
                    f"MCP server {server} is defined differently by "
                    f"{first[0]} and {name}"
                )
        types[name] = definition
    return types


@lru_cache(maxsize=1)
def preset_types() -> dict[str, AgentTypeDef]:
    """The preset type library (read-only, shipped with the platform)."""
    return load_type_library(_PRESET_DIR)
