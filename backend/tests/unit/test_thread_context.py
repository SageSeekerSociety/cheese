"""一条支线被告知了什么：房间的实况文档 + 房间最近在聊什么。

拆一条支线出去提供四样东西，第一样是**干净的上下文**。干净不等于空：一条支线要是
只拿到自己的任务简报，它就不知道自己站在哪个房间里、房间刚定了什么，于是把已经讨论
完的事再讨论一遍——这正是「一件活曾经必须是一个房间」的理由之一。

所以支线的 prompt 里有两级文档，加上房间主线最近的一段。这里钉的是那一段怎么截、
以及截掉了要不要说出来。
"""

from datetime import UTC, datetime, timedelta

from app.domain.agent.chat import (
    _ROOM_BACKDROP_BUDGET_CHARS,
    _build_system_prompt,
    _room_backdrop,
)
from app.domain.block.models import AuthorType, Block, BlockKind


def _msg(author: str, content: str, minute: int) -> Block:
    return Block(
        author=author,
        author_type=AuthorType.human,
        content=content,
        kind=BlockKind.message,
        created_at=datetime(2026, 8, 22, 10, tzinfo=UTC) + timedelta(minutes=minute),
    )


def test_the_backdrop_reads_in_the_order_it_was_said():
    """装的时候从最新往回装，读的时候还是从上往下读。

    两个方向都必要：预算要花在最近发生的事上，而人（和模型）是按时间顺序理解对话
    的。倒序渲染出来的房间背景读起来像倒放的录音。
    """
    text, dropped = _room_backdrop(
        [_msg("张衡", "先做数据清洗", 0), _msg("李甘", "我来做特征", 5)],
        _ROOM_BACKDROP_BUDGET_CHARS,
    )

    assert dropped == 0
    assert text.index("先做数据清洗") < text.index("我来做特征")
    assert "张衡" in text and "李甘" in text


def test_the_budget_is_characters_so_one_long_message_cannot_eat_the_window():
    """按字符截，不按条数。

    「最近 20 条」在一个有人贴过日志的房间里等于「最近 1 条」——那一条把窗口吃光，
    另外 19 条的位置还在，只是没有内容可放。这就是为什么这里的上限是预算。
    """
    room = [_msg("张衡", "x" * 5000, 0), _msg("李甘", "定了，就这么办", 1)]

    text, dropped = _room_backdrop(room, 200)

    assert "定了，就这么办" in text, "最新的一条必须进来"
    assert dropped == 1
    assert len(text) < 1000


def test_what_did_not_come_in_is_said_out_loud():
    """没进来的必须说出来。

    一个读者分不清「房间没说过话」和「房间说了很多但没给我」的时候，他会停止相信
    这段上下文——然后连还能拿到的那部分也不看了。记忆注入已经踩过这个坑。
    """
    room = [_msg("张衡", f"第 {i} 条讨论内容" * 10, i) for i in range(40)]
    text, dropped = _room_backdrop(room, 300)
    assert dropped > 0

    prompt = _build_system_prompt(
        "base",
        "",
        "任务简报",
        [],
        room_doc="房间共识",
        room_backdrop=text,
        room_backdrop_dropped=dropped,
    )

    assert f"更早的 {dropped} 条没放进来" in prompt


def test_a_thread_is_told_which_document_is_whose():
    """两级文档都在，而且分得清哪份是哪份。

    房间那份是共识（这个地方在干什么、定了什么），支线那份是这一件活。混在一起的
    话，分身会把房间的目标当成自己的任务，或者反过来把自己的进度写进房间的共识。
    """
    prompt = _build_system_prompt(
        "base",
        "",
        "## 这件活\n把 X 迁移到 Y",
        [],
        room_doc="## 这个房间\n我们在重做推荐系统",
        room_backdrop="- 张衡：先做数据清洗",
    )

    room_section = prompt.index("这个房间的实况文档")
    own_section = prompt.index("当前话题的实况文档")
    assert room_section < own_section, "房间那份在前：先知道站在哪儿，再看要做什么"
    assert "我们在重做推荐系统" in prompt
    assert "把 X 迁移到 Y" in prompt
    # 房间主线不是这条支线的对话，说清楚，否则分身会以为房间里的人看得见它在说什么。
    assert "这是房间主线，不是你这条支线的对话" in prompt


def test_a_room_running_its_own_turn_is_not_handed_its_own_document_twice():
    """房间自己跑一轮时，这两段都不出现——它读的本来就是自己那份。"""
    prompt = _build_system_prompt("base", "", "房间自己的文档", [])

    assert "这个房间的实况文档" not in prompt
    assert "这个房间最近在聊什么" not in prompt
    assert "房间自己的文档" in prompt
