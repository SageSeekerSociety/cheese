"""Product guidance for native Claude skills and API-only conversations."""

import json
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


# The room's chat guide is in every turn: nearly every turn speaks, so a guide
# loaded on demand was loaded on 3% of the turns that sent a message.
NATIVE_CHAT_GUIDANCE = load_skills(["chat"])


#: Skills that are already written as native Claude skills, shipped verbatim.
#:
#: The container gets these by having the whole `sandbox/skills` tree copied into
#: its session directory (`workspace.service.session_dir`); an enrolled device
#: never sees that tree, so the same file has to travel here too. Reading it from
#: one place is what stops the two paths from drifting — a skill fixed in the
#: container and stale on a device is exactly the kind of split nobody notices,
#: because both machines run and only one of them is right.
_NATIVE_SKILL_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "skills"
_SHIPPED_NATIVE_SKILLS = ("cheese", "documents", "wolfram")

#: What can travel. A skill is not one markdown file: `documents` ships the
#: scripts that do the editing and the reference files they are explained in,
#: and a skill that arrives without them is worse than one that never mentions
#: them — the agent reads a command, runs it, and gets "No such file". So the
#: whole directory travels.
#:
#: It is a list of suffixes rather than "everything there" because every path
#: carries a file as text, so a font or a screenshot would arrive corrupted
#: rather than fail. Anything added to a skill outside this list is caught by
#: `tests/unit/test_native_skill_files.py` at build time instead of silently
#: not being shipped.
SKILL_FILE_SUFFIXES = (".md", ".py", ".sh", ".txt", ".json", ".typ")


#: Folder names a project's own skill may not take: the platform ships these.
RESERVED_SKILL_NAMES = frozenset(
    {
        *_SHIPPED_NATIVE_SKILLS,
        "cheese",
        "cheese-docs",
        "cheese-chat",
    }
)


def native_skill_files() -> dict[str, str]:
    """Files relative to the session's CLAUDE_CONFIG_DIR, never its worktree."""
    files: dict[str, str] = {}
    for name in _SHIPPED_NATIVE_SKILLS:
        root = _NATIVE_SKILL_SRC / name
        if not root.is_dir():
            continue
        for source in sorted(root.rglob("*")):
            if source.suffix not in SKILL_FILE_SUFFIXES or source.is_symlink():
                continue
            relative = source.relative_to(_NATIVE_SKILL_SRC).as_posix()
            files[f"skills/{relative}"] = source.read_text(encoding="utf-8")
    # The document guide is written here rather than as a native skill folder
    # because the document agent inlines the same files (`document/question.py`).
    meta, body = _parse(_SKILL_DIR / "doc_writing.md")
    files["skills/cheese-docs/SKILL.md"] = (
        f"---\nname: cheese-docs\n"
        f"description: {json.dumps(meta['description'], ensure_ascii=False)}\n"
        f"---\n\n{body}\n"
    )
    _, blocks = _parse(_SKILL_DIR / "doc_blocks.md")
    files["skills/cheese-docs/references/blocks.md"] = blocks + "\n"
    return files
