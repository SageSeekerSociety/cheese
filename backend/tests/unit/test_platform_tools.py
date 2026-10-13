"""清单那一侧的说明里，「什么时候再写一次」是写着的。

2026-10-11 报上来的症状是清单从「全空」到「全打勾」没有中间：写它的人在开工时写一次、
收尾时再写一次，中间那 40 分钟没人动过它。工具说明当时只说「做完时再调用一次」——照着
读就是收尾那一下。平台的兜底（`remind_stale_checklists`）会叫一轮补写，但说明本身不该
把人往那个读法上带：这一条钉住的是那句话在。

说明在 `sandbox/cheese` 里，它单独发版到机器上，import 不进测试进程，所以这里读字面
量——和 `test_chat_publication.py` 读句子、`tests/contract/test_platform_tools.py` 把
调用写死在用例里是同一个道理。
"""

import re
from pathlib import Path

#: `sandbox/cheese` 在 `backend/` 的上两层：`backend/tests/unit/` 起算。
CLI = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _description(name: str) -> str:
    """The tool's own words, as the file writes them: the段落 that opens with
    the name and runs until the next ``_tool(``."""
    text = CLI.read_text(encoding="utf-8")
    start = text.index(f'"{name}"')
    end = text.index("_tool(", start + len(name))
    return text[start:end]


def test_todo_write_asks_for_a_write_after_every_finished_step() -> None:
    said = _description("todo_write")
    assert "每做完一步就再调用一次" in said
    # 中间不写的后果，就在同一句里说清楚——「做完时再调用一次」单看像收尾那一下，
    # 这正是被读成前面那样的原因。
    assert "一直看到开工那一版" in said
    assert "completed" in said and "in_progress" in said


def test_todo_write_still_says_what_it_said_before() -> None:
    """加那一句不能把原来那几件说清楚的事挤掉：谁改哪一条、结果写在哪、
    整份替换而不是增量。"""
    said = _description("todo_write")
    for still_true in ("new=true", "result", "不是增量", "in_progress"):
        assert still_true in said, still_true


def test_the_room_rule_and_the_tool_agree_that_every_step_is_written() -> None:
    """会话读的是系统提示词那一段、工具表读的是上面那段，两处说同一件事。"""
    from app.domain.agent.harness.prompt import TODO_WRITE

    assert re.search(r"别只在开工和收尾两头写", TODO_WRITE)
    assert "每做完一步" in TODO_WRITE.replace("\n", "")
