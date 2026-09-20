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


# Direct API callers have no native Skill loader.
DEFAULT_CHAT_SKILLS = ["chat", "doc-form"]

# Every response needs the chat guide; loading it as a tool can add a model round.
NATIVE_CHAT_GUIDANCE = (
    "普通输出和最终答复不会发布到聊天；用 chat_send 工具主动发送。"
    "平台操作用同名的 cheese_* 工具，没有对应工具的平台 API 用 platform_request。"
    "聊天说明已在下方提供，无需调用 Skill 工具加载。"
    "编写或更新话题文档时加载 cheese-docs。"
    "能直接回答就发送答案，需要继续处理就先发送你理解的意思和下一步。"
    "排队或执行中追加的用户消息也按此处理。"
    "巡检遵循 heartbeat 的通知规则，分身向主 agent 回报。\n\n"
    + load_skills(["chat"]).replace("doc-form", "cheese-docs")
)


#: Skills that are already written as native Claude skills, shipped verbatim.
#:
#: The container gets these by having the whole `sandbox/skills` tree copied into
#: its session directory (`workspace.service.session_dir`); an enrolled device
#: never sees that tree, so the same file has to travel here too. Reading it from
#: one place is what stops the two paths from drifting — a skill fixed in the
#: container and stale on a device is exactly the kind of split nobody notices,
#: because both machines run and only one of them is right.
_NATIVE_SKILL_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "skills"
_SHIPPED_NATIVE_SKILLS = ("documents",)

#: What can travel. A skill is not one markdown file: `documents` ships the
#: scripts that do the editing and the reference files they are explained in,
#: and a skill that arrives without them is worse than one that never mentions
#: them — the agent reads a command, runs it, and gets "No such file". So the
#: whole directory travels.
#:
#: It is a list of suffixes rather than "everything there" because of the
#: device path: each file is written through a shell heredoc, so a font or a
#: screenshot would arrive corrupted rather than fail. Anything added to a
#: skill outside this list is caught by
#: `tests/unit/test_native_skill_files.py` at build time instead of silently
#: not being shipped.
_SKILL_FILE_SUFFIXES = (".md", ".py", ".sh", ".txt", ".json", ".typ")

#: The device path ends each file's heredoc with this line, so a file
#: containing it would cut itself off at that line. The test asserts no skill
#: file does.
SKILL_HEREDOC_MARKER = "CHEESE_NATIVE_SKILL"


def native_skill_files() -> dict[str, str]:
    """Files relative to the session's CLAUDE_CONFIG_DIR, never its worktree."""
    files: dict[str, str] = {}
    for name in _SHIPPED_NATIVE_SKILLS:
        root = _NATIVE_SKILL_SRC / name
        if not root.is_dir():
            continue
        for source in sorted(root.rglob("*")):
            if source.suffix not in _SKILL_FILE_SUFFIXES or source.is_symlink():
                continue
            relative = source.relative_to(_NATIVE_SKILL_SRC).as_posix()
            files[f"skills/{relative}"] = source.read_text(encoding="utf-8")
    for source, name in (("doc_form.md", "cheese-docs"),):
        meta, body = _parse(_SKILL_DIR / source)
        body = body.replace("doc-form", "cheese-docs")
        description = meta["description"].replace("doc-form", "cheese-docs")
        description = description.replace("chat 技能", "会话内的聊天说明")
        files[f"skills/{name}/SKILL.md"] = (
            f"---\nname: {name}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"---\n\n{body}\n"
        )
    return files
