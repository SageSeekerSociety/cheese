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

# API-only conversations have no Skill tool and keep using load_skills().
NATIVE_CHAT_GUIDANCE = (
    "普通输出和最终答复不会发布到聊天；请用 cheese chat send 主动发送。"
    "回应用户消息前，用 Skill 工具加载 cheese-chat；"
    "编写或更新话题文档时加载 cheese-docs。"
    "能直接回答就发送答案，需要继续处理就先发送你理解的意思和下一步。"
    "排队或执行中追加的用户消息也按此处理。"
    "巡检遵循 heartbeat 的通知规则，分身向主 agent 回报。"
)


def native_skill_files() -> dict[str, str]:
    """Files relative to the session's CLAUDE_CONFIG_DIR, never its worktree."""
    files = {}
    for source, name in (("chat.md", "cheese-chat"), ("doc_form.md", "cheese-docs")):
        meta, body = _parse(_SKILL_DIR / source)
        body = body.replace("doc-form", "cheese-docs")
        description = meta["description"].replace("doc-form", "cheese-docs")
        description = description.replace("chat 技能", "cheese-chat 技能")
        files[f"skills/{name}/SKILL.md"] = (
            f"---\nname: {name}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"---\n\n{body}\n"
        )
    return files
