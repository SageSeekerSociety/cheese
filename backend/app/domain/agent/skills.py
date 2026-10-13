"""Product guidance for native Claude skills and API-only conversations.

The guides themselves, and the vocabulary of what is shipped to a session, live
in `app.domain.skill_library` (layer 1): a session directory is built, and a
project's skill names are policed, without going through this package. What is
here is the turn's own reading of a guide — the text, assembled into a prompt.
"""

from pathlib import Path

from app.domain.skill_library import SKILL_DIR, parse_frontmatter


def available_skills() -> dict[str, Path]:
    """Map skill `name` (from frontmatter) → file path."""
    result: dict[str, Path] = {}
    for path in sorted(SKILL_DIR.glob("*.md")):
        meta, _ = parse_frontmatter(path)
        result[meta.get("name", path.stem)] = path
    return result


def load_skills(names: list[str]) -> str:
    """Concatenate the bodies of the named skills (unknown names skipped)."""
    paths = available_skills()
    chunks: list[str] = []
    for name in names:
        path = paths.get(name)
        if path is not None:
            _, body = parse_frontmatter(path)
            chunks.append(body)
    return "\n\n---\n\n".join(chunks)


# The room's chat guide is in every turn: nearly every turn speaks, so a guide
# loaded on demand was loaded on 3% of the turns that sent a message.
NATIVE_CHAT_GUIDANCE = load_skills(["chat"])
