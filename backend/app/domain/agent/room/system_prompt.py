"""The kind of session a place runs, and the system prompt it starts with.

Both the turn that streams (`turn/intake/preparation/streaming.py`) and the
prewarm that starts a session ahead of it (`preparation/launch.py`) come here,
so the two cannot render different prompts for the same seat.
"""

import json
from typing import TYPE_CHECKING

from app.domain.agent.harness.driven.runner import (
    REPLY_HELD_ENV,
    REPLY_TOOLS,
    REPLY_TOOLS_ENV,
)
from app.domain.agent.session_host.host import keeps_memory
from app.domain.agent.skills import load_skill
from app.domain.prompts import PROFILES, Facts, Kind, render_system

if TYPE_CHECKING:
    from app.domain.room_task.place import Place


def session_kind(place: "Place", *, needs_place: bool) -> Kind:
    """Which kind of session runs at ``place``: a private chat has no place to
    work in (`needs_place`); otherwise a task's own conversation, a 支线's, or
    the channel's main line."""
    if not needs_place:
        return Kind.private
    if place.task is not None:
        return Kind.task
    if place.thread is not None:
        return Kind.thread
    return Kind.main_line


def session_system_prompt(
    base: str,
    *,
    kind: Kind,
    has_doc: bool,
    role: str | None,
    harness: str,
    name: str,
    carried: str = "",
) -> str:
    """The system prompt a session of ``kind`` starts with, for the teammate
    called ``name`` on ``harness``. ``carried``: the skill bodies the teammate
    brings (`context.carried_skills`).

    规矩进系统提示词，现状进开场快照：系统提示词在一个会话里一字不变，前缀缓存
    才接得上。"""
    return render_system(
        kind,
        Facts(
            harness=harness,
            has_doc=has_doc,
            # 记忆那一段跟着这一轮跑的骨架走：写下来的文件同步不回平台的骨架，
            # 读到它只会以为自己在写项目记忆。
            keeps_memory=keeps_memory(harness),
            name=name,
            role=role,
        ),
        skill=load_skill,
        base=base,
        carried=carried,
    )


#: What a session that holds fewer tools reads them from (`sandbox/cheese`
#: `TOOLS_ENV`, Claude Code's launch for its own tools), and where one that
#: writes only its memory is told so (`proxy.js`).
TOOLS_ENV = "CHEESE_TOOLS"
WRITES_ENV = "CHEESE_WRITES"


def session_env(kind: Kind) -> dict[str, str]:
    """The launch's environment a session of ``kind`` differs by: the tools
    it holds when it holds fewer, where it may write, and how a person's
    message to it is answered. Part of the launch, so a session started
    before its kind's profile changed is replaced at the next boundary. A
    kind that holds the defaults adds nothing."""
    p = PROFILES[kind]
    env: dict[str, str] = {}
    if p.narrowed:
        env[TOOLS_ENV] = json.dumps(sorted(p.tools))
    if p.writes is not None:
        env[WRITES_ENV] = p.writes
    if tuple(p.reply_tools) != tuple(REPLY_TOOLS):
        env[REPLY_TOOLS_ENV] = ",".join(p.reply_tools)
    if not p.reply_held:
        env[REPLY_HELD_ENV] = "0"
    return env
