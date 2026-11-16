"""Dump, as JSON, what every kind of session reads — rendered by the code that
renders it for the platform.

The prompts live in ``backend/app/domain/prompts``: the prose in
``pieces/*.md``, and one profile per kind of session saying which pieces it
reads, when, which skills follow, what it is told after every message and which
tools it holds. That package is standard-library only, so the docs build machine
(no backend dependencies) imports it for real and renders each profile; the page
(``docs/manual/dev/ref-prompt.md``) shows what comes out, so it cannot drift from
what a session reads.

``blocks`` is the task session's opening, every switch on, split at the
line-start ``## `` that marks a block: the context page's timeline is bound to
it (``prompt-blocks``).
"""

import json
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.prompts import (  # noqa: E402
    PROFILES,
    Facts,
    Kind,
    check,
    piece,
    render_opening,
    render_system,
)
from app.domain.prompts.profile import BASE, CARRIED, SKILLS  # noqa: E402
from app.domain.prompts.text import PIECES  # noqa: E402

SKILL_LIBRARY = ROOT / "backend" / "app" / "domain" / "skill_library"
BASE_TEXT = "（底稿：`settings.agent_system_prompt`，部署时配置的那一段开头）"

LABELS = {
    Kind.main_line: "频道主线",
    Kind.task: "任务",
    Kind.thread: "支线",
    Kind.private: "私聊",
    Kind.document: "文档里的芝士",
    Kind.personal: "个人芝士",
    Kind.dream_team: "整理项目记忆",
    Kind.dream_private: "整理个人记忆",
    Kind.situation: "写项目现状",
}

#: The facts every kind is shown with: every switch on.
FACTS = Facts(
    harness="claude-code", has_doc=True, keeps_memory=True, name="芝士", role="你是推荐系统方面的专家。"
)
#: Slot values the non-room kinds take, as plain samples.
VALUES = {
    Kind.document: {
        "agent": "芝士",
        "place": piece("document/where_thread"),
        "answer": piece("document/answer_thread"),
        "machine": piece("document/no_machine"),
        "goals": "要做成：把推荐算法做成一个能演示的原型。",
        "memory": "- [构建用 npm ci](build.md) — 不要用 npm install",
    },
    Kind.personal: {"computer": True},
}


def parse(path: Path) -> tuple[dict[str, str], str]:
    """A skill guide's frontmatter and body, split the way `skill_library` does."""
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
    return meta, body.strip()


LIBRARY = {parse(p)[0].get("name", p.stem): p for p in sorted(SKILL_LIBRARY.glob("*.md"))}


def skill(name: str) -> str:
    path = LIBRARY.get(name)
    return parse(path)[1] if path else ""


class _Week:
    mode = "guide"
    system_prompt = "课上讲到了循环和数组，不要用递归。"
    space_prompt = None
    learning_goals = ["能自己写出循环", "能讲清数组和循环怎么配合"]
    private_prompt = "参考答案在第 6 周讲义第 12 页；评分只看能否独立跑通，不看代码风格。"
    skills: list[str] = []

    @property
    def is_empty(self) -> bool:
        return False

    def fill(self, template: str) -> str:
        return template.replace("{learning_goals}", "、".join(self.learning_goals))


class _Index:
    def __init__(self, sections):
        self.sections = [types.SimpleNamespace(**s) for s in sections]
        self.warnings: list[str] = []

    def is_empty(self) -> bool:
        return not any(s.text.strip() for s in self.sections)


OPENING = dict(
    thread="你在频道「#前端」的一条支线里。这里的人 @ 你，你才回答。",
    channel="你在频道「#前端」的主线里。",
    tasks="## 这个频道里还在进行的任务\n- #12 接口联调　进行中　提出人：李工（handle: ligong）　负责队友：芝士（handle: cheese）",
    teaching=types.SimpleNamespace(
        course="创研课 2026 秋",
        teaching=_Week(),
        materials=[{"id": 7, "name": "第 6 周讲义", "url": "https://example.com/week6.pdf"}],
        documents=[{"id": "0b6d6a1e-3c1f-4f0a-9a52-6d1f2c3b4a5e", "title": "推荐系统入门"}],
    ),
    stages=[
        types.SimpleNamespace(index=0, name="需求分析", description="交一份需求文档", due_at=datetime(2026, 11, 1, 15, 59, tzinfo=timezone.utc), status="PASSED"),
        types.SimpleNamespace(index=1, name="实现", description="", due_at=None, status="NOT_SUBMITTED"),
    ],
    topics=[{"title": "搭建推荐算法原型"}, {"title": "接口联调"}],
    artifacts=[{"name": "实验报告", "version": 3, "about": "交给老师的那份结题报告", "id": "a1b2c3"}],
    roster=[
        {"name": "张衡", "handle": "zhangheng", "source": "owner", "agent": False, "active": True},
        {"name": "李工", "handle": "ligong", "source": "team", "agent": False, "active": True},
        {"name": "芝士", "handle": "cheese", "source": "team", "agent": True, "active": True},
    ],
    overview_doc="要做成：把推荐算法做成一个能演示的原型。时间：11 月 15 日中期答辩前。这阶段不做：线上部署。",
    overview_doc_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
    doc="目标：本周内跑通推荐流程。当前：数据已就位，模型还在调。",
    memory=_Index([{"label": "项目共享（team）", "prefix": "team", "text": "- [构建用 npm ci](build.md) — 不要用 npm install"}]),
    keeps_memory=True,
    environment=["- 这台机器：内存 4GB、2 核。"],
)


def unwrapped(notice: str) -> str:
    """A notice's text without the tag around it: on the page the tag would
    only glue itself to the block in front of it."""
    lines = notice.split("\n")
    if lines and lines[0].startswith("<platform_notice") and lines[-1] == "</platform_notice>":
        return "\n".join(lines[1:-1])
    return notice


def split_blocks(text: str) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## ") and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    out = [b for b in ("\n".join(block).strip("\n") for block in blocks) if b.strip()]
    # What comes before the first heading (the name, the deployment's opening
    # paragraph) is one block per paragraph: each is a part of its own.
    if out and not out[0].startswith("## "):
        out[:1] = [part for part in out[0].split("\n\n") if part.strip()]
    return out


def condition(inc) -> str:
    said = (inc.when.__doc__ or "").strip().splitlines()[0] if inc.when.__doc__ else ""
    parts = [] if said in ("", "总在") else [said]
    parts += [f"给了 `{name}`" for name in inc.needs]
    return "；".join(parts) or "总在"


def part_name(inc) -> str:
    if inc.piece == BASE:
        return "底稿（部署时配置的开头一段）"
    if inc.piece == SKILLS:
        return "技能正文"
    return inc.piece


def kind_entry(kind: Kind) -> dict:
    check(kind)
    p = PROFILES[kind]
    system = render_system(
        kind, FACTS, skill=skill, base=BASE_TEXT, carried="", values=VALUES.get(kind, {})
    )
    room = p.notice is not None
    opening = unwrapped(render_opening(kind, **OPENING).text) if room else ""
    return {
        "kind": str(kind),
        "label": LABELS[kind],
        "parts": [
            {"piece": part_name(inc), "file": f"backend/app/domain/prompts/pieces/{inc.piece}.md" if inc.piece not in (BASE, SKILLS) else "", "condition": condition(inc)}
            for inc in p.system
        ],
        "skills": [("（队友自己带的技能）" if s == CARRIED else s) for s in p.skills],
        "system": system,
        "chars": len(system),
        "opening_sections": sorted(p.opening),
        "opening": opening,
        "notice": {
            lang: piece(f"{p.notice}.{lang}") for lang in ("zh", "en")
        } if p.notice else {},
        "tools": sorted(p.tools),
    }


def main() -> None:
    kinds = [kind_entry(kind) for kind in PROFILES]
    task = next(k for k in kinds if k["kind"] == "task")
    blocks = []
    for text in split_blocks(f"{task['system']}\n\n{task['opening']}"):
        head = text.split("\n", 1)[0]
        blocks.append({"title": head.lstrip("#").strip(), "chars": len(text), "text": text})
    library = []
    for name, path in LIBRARY.items():
        meta, body = parse(path)
        library.append(
            {
                "file": f"backend/app/domain/skill_library/{path.name}",
                "name": name,
                "title": meta.get("title", path.stem),
                "description": meta.get("description", ""),
                "body": body,
                "chars": len(body),
            }
        )
    pieces = sorted(str(p.relative_to(PIECES))[:-3] for p in PIECES.rglob("*.md"))
    json.dump(
        {"kinds": kinds, "blocks": blocks, "library": {"files": library}, "pieces": pieces},
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
