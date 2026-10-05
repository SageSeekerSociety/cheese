"""记忆那一段，只在真会把记忆对账回去的骨架里出现。

`MEMORY_INSTRUCTIONS` 说的是一件事：**写 `~/.cheese/memory/` 下的文件**。而这
件事只有支持对账的骨架做得到——写下去的文件要有人带回平台、下一轮再铺回来。带不回
去的骨架（codex、pi）读到的是一条做不到的说明：它照着写，文件留在会话机上，下一轮
什么都不在，而它一个字都不知道为什么。

所以这一段不由提示词自己猜，由调用方按 runtime 的能力传进来
（`session_host/host.py` 的 `keeps_memory`，读的是骨架注册表）。
"""

from app.domain.agent.harness.prompt import build_session_opening, build_system_prompt
from app.domain.memory.files_store import IndexSection, MemoryIndex
from app.domain.memory.instructions import MEMORY_INSTRUCTIONS

INDEX = MemoryIndex(
    sections=[IndexSection(label="项目", prefix="team", text="- [甲](a.md) — 记忆甲")],
    warnings=[],
)
#: 说明书的第一行，和索引那一块的开头。整份说明书不在这里比对（它是文案，会
#: 改），但「这一段在不在」必须看得出来。
INSTRUCTIONS_HEAD = "## 记忆（memory）"
INDEX_HEAD = "## 你的记忆（索引"


def _told(index: MemoryIndex | None, *, keeps_memory: bool = False) -> str:
    """一条新会话开场听到的全部：系统提示词（说明书）加开场快照（索引）。"""
    system = build_system_prompt(
        "底稿", "技能", has_doc=True, keeps_memory=keeps_memory
    )
    opening = build_session_opening(
        doc="## 目标\n\n做一件事。", memory=index, keeps_memory=keeps_memory
    )
    return system + "\n\n" + opening.text


def _prompt(**kwargs) -> str:
    return _told(INDEX, **kwargs)


def test_a_harness_that_keeps_no_memory_is_not_told_about_it():
    """索引和说明书同进同出：一条读得到、改不回去的索引，只会让 agent 去改一个它
    写不回去的地方。"""
    prompt = _prompt()

    assert INSTRUCTIONS_HEAD not in prompt
    assert INDEX_HEAD not in prompt
    assert "记忆甲" not in prompt


def test_a_harness_that_keeps_memory_is_told_how_it_works():
    prompt = _prompt(keeps_memory=True)

    assert MEMORY_INSTRUCTIONS in prompt
    assert INSTRUCTIONS_HEAD in prompt
    assert INDEX_HEAD in prompt
    assert "记忆甲" in prompt


def test_the_instructions_do_not_need_an_index_to_be_there():
    """新项目第一个回合：一条记忆都还没有，索引是空的，而说明书正在最有用的时候
    ——第一条记忆该写成什么样，只有它说了。"""
    prompt = _told(None, keeps_memory=True)

    assert MEMORY_INSTRUCTIONS in prompt
    assert INDEX_HEAD not in prompt


def test_an_empty_index_is_not_a_block():
    empty = MemoryIndex(sections=[], warnings=[])
    prompt = _told(empty, keeps_memory=True)

    assert MEMORY_INSTRUCTIONS in prompt
    assert INDEX_HEAD not in prompt
