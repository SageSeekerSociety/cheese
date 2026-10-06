"""索引两边都改过时按行合并：加的都留下，删的都删掉，同一行两种改法才算冲突。"""

import pytest

from app.domain.memory.tree import merge_lines

_A = "- [甲](a.md) — 甲\n"
_B = "- [乙](b.md) — 乙\n"
_C = "- [丙](c.md) — 丙\n"
_D = "- [丁](d.md) — 丁\n"
_E = "- [戊](e.md) — 戊\n"


@pytest.mark.parametrize(
    ("base", "ours", "theirs", "merged"),
    [
        # 两边各在末尾加一行：两行都在，先自己的。
        (_A + _B, _A + _B + _C, _A + _B + _D, _A + _B + _C + _D),
        # 一边删一行、一边在别处加一行：删的删掉，加的留下。
        (_A + _B + _C, _A + _C, _A + _B + _C + _D, _A + _C + _D),
        # 两边删了不同的行：都删掉。
        (_A + _B + _C, _B + _C, _A + _B, _B),
        # 两边做了同一件事：只做一次。
        (_A + _B, _A + _B + _C, _A + _B + _C, _A + _B + _C),
        # 一边在开头加，一边在末尾加。
        (_A + _B, _E + _A + _B, _A + _B + _D, _E + _A + _B + _D),
        # 底稿没有结尾的换行。
        (_A.rstrip("\n"), _A + _C, _A + _D, _A + _C + _D),
    ],
)
def test_lines_added_or_removed_on_either_side_all_land(base, ours, theirs, merged):
    assert merge_lines(base, ours, theirs) == merged


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        # 同一行改成了两个说法。
        (_A + "- [乙](b.md) — 乙，改过\n", _A + "- [乙](b.md) — 乙，另一种改法\n"),
        # 同一行一边改、一边删。
        (_A + "- [乙](b.md) — 乙，改过\n", _A),
    ],
)
def test_one_line_changed_two_ways_does_not_merge(ours, theirs):
    assert merge_lines(_A + _B, ours, theirs) is None
