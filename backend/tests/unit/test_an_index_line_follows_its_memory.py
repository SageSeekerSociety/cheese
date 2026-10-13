"""索引里新加的一行跟着它指的那条记忆走。

一条新记忆超了长度上限没收，它在同一轮里加进 `MEMORY.md` 的那一行也不能收：收
下了，平台的索引就指向一个不存在的文件，每次同步铺到所有房间，读它的 agent 读到
「文件不存在」。别人早先留下的行不动，哪怕它也指空。
"""

from app.domain.memory.files import digest
from app.domain.memory.tree import sync_tree

_INDEX = "- [a](a.md) — 原来那一条\n"
_A = "---\nname: a\ndescription: 旧的\ntype: project\n---\n\n原来那一版。\n"


def _note(body: str) -> str:
    return f"---\nname: b\ndescription: 一条\ntype: project\n---\n\n{body}\n"


def _round(prefix: str, disk: dict[str, str], platform: dict[str, str]):
    """平台这一份自上次以来没动过；会话把磁盘写成了 ``disk``。"""
    return sync_tree(
        scopes={prefix: platform},
        disk={f"{prefix}/{name}": body for name, body in disk.items()},
        baseline={f"{prefix}/{name}": digest(body) for name, body in platform.items()},
    )


def test_a_rejected_new_memory_takes_its_new_index_line_with_it():
    index = _INDEX + "- [b](b.md) — 新记的一条\n"
    result = _round(
        "team",
        {"MEMORY.md": index, "a.md": _A, "b.md": _note("字" * 1001)},
        {"MEMORY.md": _INDEX, "a.md": _A},
    )
    assert "team/b.md" in result.rejected
    assert "team/b.md" not in result.files
    assert result.files["team/MEMORY.md"] == _INDEX
    assert result.baseline["team/MEMORY.md"] == digest(_INDEX)


def test_an_accepted_new_memory_keeps_its_index_line():
    index = _INDEX + "- [b](b.md) — 新记的一条\n"
    b = _note("短的一条。")
    result = _round(
        "team",
        {"MEMORY.md": index, "a.md": _A, "b.md": b},
        {"MEMORY.md": _INDEX, "a.md": _A},
    )
    assert result.files["team/b.md"] == b
    assert result.files["team/MEMORY.md"] == index


def test_a_new_line_pointing_at_a_file_never_written_is_not_taken():
    index = _INDEX + "- [c](c.md) — 文件没写\n"
    result = _round(
        "team", {"MEMORY.md": index, "a.md": _A}, {"MEMORY.md": _INDEX, "a.md": _A}
    )
    assert result.files["team/MEMORY.md"] == _INDEX


def test_a_line_already_on_the_platform_is_left_alone_even_if_it_points_nowhere():
    old = _INDEX + "- [gone](gone.md) — 早先就在\n"
    edited = old + "- [b](b.md) — 新记的一条\n"
    b = _note("短的一条。")
    result = _round(
        "team",
        {"MEMORY.md": edited, "a.md": _A, "b.md": b},
        {"MEMORY.md": old, "a.md": _A},
    )
    assert result.files["team/MEMORY.md"] == edited


def test_a_private_scope_follows_the_same_rule():
    index = _INDEX + "- [b](b.md) — 新记的一条\n"
    result = _round(
        "private/someone",
        {"MEMORY.md": index, "a.md": _A, "b.md": _note("字" * 1001)},
        {"MEMORY.md": _INDEX, "a.md": _A},
    )
    assert "private/someone/b.md" in result.rejected
    assert result.files["private/someone/MEMORY.md"] == _INDEX
