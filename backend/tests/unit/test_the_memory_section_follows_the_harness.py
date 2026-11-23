"""记忆那一段，只在真会把记忆对账回去的骨架里出现。

记忆的规矩说的是一件事：**写 `~/.cheese/memory/` 下的文件**。而这件事只有支持
对账的骨架做得到——写下去的文件要有人带回平台、下一轮再铺回来。带不回去的骨架
（codex、pi）读到的是一条做不到的说明：它照着写，文件留在会话机上，下一轮什么都
不在，而它一个字都不知道为什么。

所以这一段不由提示词自己猜，由调用方按 runtime 的能力传进来
（`session_host/host.py` 的 `keeps_memory`，读的是骨架注册表）。
"""

from app.domain.agent.room.system_prompt import session_system_prompt
from app.domain.memory.files_store import IndexSection, MemoryIndex
from app.domain.prompts import Facts, Kind, piece, render_opening, render_system

INDEX = MemoryIndex(
    sections=[IndexSection(label="项目", prefix="team", text="- [甲](a.md) — 记忆甲")],
    warnings=[],
)


def _told(index: MemoryIndex | None, *, harness: str) -> tuple[str, dict[str, str]]:
    """一条新会话开场听到的：系统提示词（规矩）和开场的各段（索引）。"""
    from app.domain.agent.session_host.host import keeps_memory

    system = session_system_prompt(
        "底稿", kind=Kind.task, has_doc=True, role=None, harness=harness, name="芝士"
    )
    opening = render_opening(
        Kind.task,
        doc="## 目标\n\n做一件事。",
        memory=index,
        keeps_memory=keeps_memory(harness),
    )
    return system, opening.sections


def test_a_harness_that_keeps_no_memory_is_not_told_about_it():
    """索引和规矩同进同出：一条读得到、改不回去的索引，只会让 agent 去改一个它
    写不回去的地方。"""
    system, sections = _told(INDEX, harness="codex")

    assert piece("memory/multi_person") not in system
    assert "memory" not in sections


def test_a_harness_that_keeps_memory_is_told_how_it_works():
    system, sections = _told(INDEX, harness="claude-code")

    assert piece("memory/multi_person") in system
    assert "记忆甲" in sections["memory"]


def test_the_rules_do_not_need_an_index_to_be_there():
    """新项目第一个回合：一条记忆都还没有，索引是空的，而规矩正在最有用的时候
    ——第一条记忆该写成什么样，只有它说了。"""
    system, sections = _told(None, harness="claude-code")

    assert piece("memory/multi_person") in system
    assert "memory" not in sections


def test_an_empty_index_is_not_a_section():
    system, sections = _told(
        MemoryIndex(sections=[], warnings=[]), harness="claude-code"
    )

    assert piece("memory/multi_person") in system
    assert "memory" not in sections


def test_each_kind_of_place_reads_the_memory_rules_of_its_own():
    """私聊读带整份 private 的那套规矩；频道主线、任务、讨论串读多人那套：在那里
    private 只有这一轮答复的成员的 communication 一类。两套不混着读。"""
    facts = Facts(memory_synced=True)
    private = render_system(Kind.private, facts, skill=lambda _name: "")
    assert piece("memory/private_chat") in private
    assert piece("memory/multi_person") not in private
    for kind in (Kind.main_line, Kind.task, Kind.thread):
        system = render_system(kind, facts, skill=lambda _name: "")
        assert piece("memory/multi_person") in system
        assert piece("memory/private_chat") not in system
