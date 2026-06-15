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


def available_skills() -> dict[str, Path]:
    """Map skill `name` (from frontmatter) → file path."""
    result: dict[str, Path] = {}
    for path in sorted(_SKILL_DIR.glob("*.md")):
        meta, _ = _parse(path)
        result[meta.get("name", path.stem)] = path
    return result


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
