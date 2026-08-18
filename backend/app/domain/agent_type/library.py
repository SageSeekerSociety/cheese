"""The built-in agent types, shipped as files.

An agent *type* is 出厂设置: everything true about an agent before it has worked
anywhere — who it is (the system prompt), what it can reach (skills, MCP
servers) and how it runs (model, effort, harness). It carries no memory and
belongs to no project, which is exactly what makes it shareable across them.

Types are defined Claude Code agents-style: a markdown file with YAML
frontmatter whose body is the system prompt. Presets ship as files in
``presets/``; anything a person defines lives in the ``agent_types`` table.
Resolution order is custom (DB) > preset file > None — a custom type shadows a
preset of the same name, so a project can override a preset without forking it.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class AgentTypeDef:
    """One agent type, however it was defined (preset file or DB row).

    The two sources resolve to this same shape on purpose: every consumer asks
    "what is this type" and must not care which half of the catalog answered.
    """

    name: str
    title: str
    description: str
    # The markdown body = the system prompt this agent runs under.
    body: str
    # Skills loaded at start, and MCP servers reachable — the tool surface.
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    # How it runs. None = whatever the platform would have used anyway; a type
    # that does not care must not pin the deployment's choice.
    model: str | None = None
    effort: str | None = None
    harness: str | None = None


_PRESET_DIR = Path(__file__).resolve().parent / "presets"


def parse_type_markdown(text: str) -> tuple[dict[str, str], str]:
    """Split a type file into (frontmatter, body).

    The frontmatter is the flat ``key: value`` mapping between two ``---``
    fences — the only shape the presets use — so it's parsed by hand rather
    than pulling in a YAML dependency. Quoted values are unquoted; comment and
    blank lines are skipped. Text without a leading fence is all body.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    meta: dict[str, str] = {}
    body_start = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        v = value.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        meta[key.strip()] = v
    return meta, "\n".join(lines[body_start:]).strip()


def parse_list_value(raw: str) -> list[str]:
    """A comma-separated frontmatter value as a list, blanks dropped."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_type_library(directory: Path) -> dict[str, AgentTypeDef]:
    """Load every ``*.md`` type file in *directory*, keyed by name.

    The frontmatter ``name`` wins; the filename stem is the fallback. A file
    with an empty body defines no agent and is skipped.
    """
    types: dict[str, AgentTypeDef] = {}
    if not directory.is_dir():
        return types
    for path in sorted(directory.glob("*.md")):
        meta, body = parse_type_markdown(path.read_text(encoding="utf-8"))
        if not body:
            continue
        name = meta.get("name") or path.stem
        types[name] = AgentTypeDef(
            name=name,
            title=meta.get("title", name),
            description=meta.get("description", ""),
            body=body,
            skills=parse_list_value(meta.get("skills", "")),
            mcp_servers=parse_list_value(meta.get("mcp_servers", "")),
            model=meta.get("model") or None,
            effort=meta.get("effort") or None,
            harness=meta.get("harness") or None,
        )
    return types


@lru_cache(maxsize=1)
def preset_types() -> dict[str, AgentTypeDef]:
    """The preset type library (read-only, shipped with the platform)."""
    return load_type_library(_PRESET_DIR)
