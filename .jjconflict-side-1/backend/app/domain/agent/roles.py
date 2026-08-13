"""Expert roles (spec §8.2).

芝士 isn't one persona — a project loads an expert role whose description is
prepended to 芝士's system prompt so the same agent behaves like a domain
expert.

Roles are defined Claude Code agents-style: a markdown file with YAML
frontmatter (``name`` / ``title`` / ``description``) whose body is the persona
system prompt. Built-in roles ship as files in ``role_library/``; custom roles
(created by an institution or an individual) live in the ``custom_roles``
table. Resolution order: custom (DB) > built-in file library > None — a custom
role shadows a built-in with the same name.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RoleDef:
    """One role from the file library (Claude Code agents format)."""

    name: str
    title: str
    description: str
    # The markdown body = the persona system prompt injected for 芝士.
    body: str


_LIBRARY_DIR = Path(__file__).resolve().parent / "role_library"

DEFAULT_ROLE = "fullstack-engineer"


def parse_role_markdown(text: str) -> tuple[dict[str, str], str]:
    """Split a role file into (frontmatter, body).

    The frontmatter is the flat ``key: value`` mapping between two ``---``
    fences — the only shape the library uses — so it's parsed by hand rather
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


def load_role_library(directory: Path) -> dict[str, RoleDef]:
    """Load every ``*.md`` role file in *directory*, keyed by role name.

    The frontmatter ``name`` wins; the filename stem is the fallback. A file
    with an empty body defines no persona and is skipped.
    """
    roles: dict[str, RoleDef] = {}
    if not directory.is_dir():
        return roles
    for path in sorted(directory.glob("*.md")):
        meta, body = parse_role_markdown(path.read_text(encoding="utf-8"))
        if not body:
            continue
        name = meta.get("name") or path.stem
        roles[name] = RoleDef(
            name=name,
            title=meta.get("title", name),
            description=meta.get("description", ""),
            body=body,
        )
    return roles


@lru_cache(maxsize=1)
def builtin_roles() -> dict[str, RoleDef]:
    """The built-in role library (read-only, shipped with the platform)."""
    return load_role_library(_LIBRARY_DIR)


def role_description(name: str | None) -> str | None:
    """Built-in persona prompt for *name*, or None if unknown/unset.

    Sync, file-library only — DB-backed custom roles need a session; use
    :func:`resolve_role_description` wherever one is available.
    """
    if not name:
        return None
    role = builtin_roles().get(name)
    return role.body if role else None


async def resolve_role_description(
    session: AsyncSession, name: str | None
) -> str | None:
    """Persona prompt for *name*: custom (DB) > built-in library > None."""
    if not name:
        return None
    # Local import: the expert_role domain imports builtin_roles from here.
    from app.domain.expert_role.repositories import CustomRoleRepository

    custom = await CustomRoleRepository(session).get_by_name(name)
    if custom is not None:
        return custom.body
    return role_description(name)
