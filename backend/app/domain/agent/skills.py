"""Skill library loader (spec §8.2/§8.3).

Skills are the product's "soul": markdown files teaching 芝士 things Claude Code
doesn't know by default (doc form, conversation style, activity digestion,
inspection, accept routing, upgrade judgment). We compose the relevant skills
into the agent's system prompt per scenario. (Later these can migrate to native
`.claude/skills/` loading; the markdown is already in that shape.)
"""

from pathlib import Path

_SKILL_DIR = Path(__file__).parent / "skill_library"


def _parse(path: Path) -> tuple[dict[str, str], str]:
    """Split a `--- frontmatter --- body` markdown file."""
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
    return meta, body.strip()


def _parse_tags(value: str) -> list[str]:
    """`scenarios: [accept, stage:working]` → `["accept", "stage:working"]`.

    Brackets optional, so both the existing `[accept]` style and a bare
    comma-separated list work.
    """
    return [tag.strip() for tag in value.strip().strip("[]").split(",") if tag.strip()]


def available_skills() -> dict[str, Path]:
    """Map skill `name` (from frontmatter) → file path."""
    result: dict[str, Path] = {}
    for path in sorted(_SKILL_DIR.glob("*.md")):
        meta, _ = _parse(path)
        result[meta.get("name", path.stem)] = path
    return result


def skills_for_scenario(scenario: str) -> list[str]:
    """Skill names whose frontmatter `scenarios:` lists `scenario`, in filename
    order.

    The `scenarios:` field has existed on every skill since the library was
    written but nothing ever read it — `load_skills` only ever took explicit
    names. This makes it the real selector, so one skill can serve several
    scenarios (e.g. the 递卡/token guidance applies to more than one stage)
    without duplicating it into several hardcoded name lists.
    """
    return [name for name, _ in _matching(scenario)]


def load_scenario(scenario: str) -> str:
    """Concatenated bodies of every skill tagged with `scenario` (may be "").

    Runs on every chat turn, so it reads the library in ONE pass rather than
    resolving names and then re-parsing the files to get their bodies.
    """
    return "\n\n---\n\n".join(body for _, body in _matching(scenario))


def _matching(scenario: str) -> list[tuple[str, str]]:
    """[(name, body)] for the skills tagged with `scenario`, in filename order."""
    hits: list[tuple[str, str]] = []
    for path in sorted(_SKILL_DIR.glob("*.md")):
        meta, body = _parse(path)
        if scenario in _parse_tags(meta.get("scenarios", "")):
            hits.append((meta.get("name", path.stem), body))
    return hits


def load_skills(names: list[str]) -> str:
    """Concatenate the bodies of the named skills (unknown names skipped)."""
    paths = available_skills()
    chunks: list[str] = []
    for name in names:
        path = paths.get(name)
        if path is not None:
            _, body = _parse(path)
            chunks.append(body)
    return "\n\n---\n\n".join(chunks)


# Default skills loaded for an in-topic conversation.
DEFAULT_CHAT_SKILLS = ["conversation-style", "doc-form"]
