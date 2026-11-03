"""The platform's skill guides, and what a session is shipped of them.

The markdown next to this file is the platform's own prompt text, and five of
them are also packed into skills and sent to a session: `doc_writing.md`,
`doc_blocks.md` and `doc_live.md` into `cheese-docs`, `miniapp.md` and
`doc_apps.md` into `miniapp`. The reader of
that directory and the vocabulary of what travels are therefore the same thing,
and both are read by packages further down than the one that writes prompts —
`repository` builds a session directory, `project_skill` refuses a name the
platform owns — which is why they sit here and not inside the agent layer that
first needed them.
"""

import functools
import json
import uuid
from pathlib import Path

from app.core.config import settings

#: Where the guides live: next to this file.
SKILL_DIR = Path(__file__).parent


def project_skills_root(project_id: uuid.UUID | str) -> Path:
    """Where a project's confirmed skills are mirrored on disk for a session to
    read: one directory per project under the platform's workspace.

    Written by `project_skill.service`, rebuilt after every change. The
    convention lives here, below both the writer and one of its readers —
    `repository.service.session_dir` copies that directory into a session
    directory — because where a path is is not worth an upward import;
    `event_author` and `event_about` came down here for the same reason.
    `project_skill.service` keeps its own name for the call, `mirror_root`.
    """
    return Path(settings.workspace_root) / ".project-skills" / str(project_id)


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
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


#: Skills that are already written as native Claude skills, shipped verbatim.
#:
#: A session directory gets these by having the whole `sandbox/skills` tree
#: copied into it (`repository.service.session_dir`); an enrolled device never
#: sees that tree, so the same file has to travel here too. Reading it from one
#: place is what stops the two paths from drifting — a skill fixed in one and
#: stale on a device is exactly the kind of split nobody notices,
#: because both machines run and only one of them is right.
_NATIVE_SKILL_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "skills"
_SHIPPED_NATIVE_SKILLS = (
    "cheese",
    "documents",
    "showcase",
    "wolfram",
    "lean",
    "skill-creator",
    "mcp-builder",
)

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
SKILL_FILE_SUFFIXES = (
    ".md",
    ".txt",
    ".py",
    ".sh",
    ".js",
    ".mjs",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".html",
    ".css",
    ".xml",
    ".xsd",
    ".typ",
)


#: Folder names a project's own skill may not take: the platform ships these.
RESERVED_SKILL_NAMES = frozenset({*_SHIPPED_NATIVE_SKILLS, "cheese-docs", "miniapp"})

#: Platform skills machines received before each one kept a list of what it was
#: shipped (`.cheese-platform-skills`). A machine with no list yet is treated as
#: having been shipped these, so the ones no longer shipped are removed there too.
SKILLS_SHIPPED_BEFORE_THE_LIST = ("cheese-chat", "chat-detail", "cheese-writing")


def shipped_skill_names() -> list[str]:
    """The platform skill folders a session is shipped now."""
    return sorted({path.split("/")[1] for path in native_skill_files()})


def native_skill_files() -> dict[str, str]:
    """Files relative to the session's CLAUDE_CONFIG_DIR, never its worktree."""
    return dict(_native_skill_files())


def native_skill_dir(name: str) -> Path | None:
    """The directory a shipped native skill lives in, or None for a name that is
    not one of ours or whose directory is missing."""
    if name not in _SHIPPED_NATIVE_SKILLS:
        return None
    root = _NATIVE_SKILL_SRC / name
    return root if root.is_dir() else None


def native_skill_doc(name: str) -> tuple[str, str] | None:
    """`(description, body)` of a shipped native skill's SKILL.md.

    ``None`` when the name is not one of ours or the file is missing — the
    caller treats both the same way: nothing to say about it.
    """
    root = native_skill_dir(name)
    if root is None:
        return None
    path = root / "SKILL.md"
    if not path.is_file():
        return None
    meta, body = parse_frontmatter(path)
    return meta.get("description", ""), body


def native_skill_bodies(names: list[str]) -> str:
    """The instructions of the named native skills, for a room that switched
    them on: the same text the executor already holds, put into the turn so
    every agent in the conversation follows it from its first word."""
    chunks: list[str] = []
    for name in names:
        doc = native_skill_doc(name)
        if doc is not None:
            chunks.append(doc[1])
    return "\n\n---\n\n".join(chunks)


# Read once per process: these files ship inside the image and cannot change
# while it runs, and every tool call a session makes asks for them again
# (`machine.session_work`), each time on the event loop every request shares.
@functools.cache
def _native_skill_files() -> dict[str, str]:
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
    meta, body = parse_frontmatter(SKILL_DIR / "doc_writing.md")
    files["skills/cheese-docs/SKILL.md"] = (
        f"---\nname: cheese-docs\n"
        f"description: {json.dumps(meta['description'], ensure_ascii=False)}\n"
        f"---\n\n{body}\n"
    )
    _, blocks = parse_frontmatter(SKILL_DIR / "doc_blocks.md")
    files["skills/cheese-docs/references/blocks.md"] = blocks + "\n"
    _, live = parse_frontmatter(SKILL_DIR / "doc_live.md")
    files["skills/cheese-docs/references/live-doc.md"] = live + "\n"
    # Mini-apps are their own skill because they are not only a document's:
    # one can be sent straight into a chat, and a skill named for documents is
    # the one an agent about to answer in a chat would not think to load.
    meta, body = parse_frontmatter(SKILL_DIR / "miniapp.md")
    files["skills/miniapp/SKILL.md"] = (
        f"---\nname: miniapp\n"
        f"description: {json.dumps(meta['description'], ensure_ascii=False)}\n"
        f"---\n\n{body}\n"
    )
    _, apps = parse_frontmatter(SKILL_DIR / "doc_apps.md")
    files["skills/miniapp/references/apps.md"] = apps + "\n"
    return files
