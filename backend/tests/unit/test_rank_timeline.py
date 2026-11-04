"""资料库排序框那两档的「位置」：一条只比时间的时间线。

位置是字符串，按字符串从小到大就是显示顺序；它同时是翻页的游标，所以「读回来的是
哪一刻」必须和「写进去的」对得上，否则往下翻会漏行或者原地打转。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core import rank
from app.core.errors import UnprocessableEntityError

EARLY = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
LATE = EARLY + timedelta(hours=3)


def test_descending_puts_the_later_moment_first():
    newer = rank.timeline(LATE, "b", descending=True)
    older = rank.timeline(EARLY, "a", descending=True)
    assert newer < older


def test_ascending_puts_the_earlier_moment_first():
    assert rank.timeline(EARLY, "b", descending=False) < rank.timeline(
        LATE, "a", descending=False
    )


def test_at_the_same_moment_the_name_decides_both_ways():
    """升降只翻时间，不翻名字：同一刻永远按名字从小到大。"""
    for descending in (True, False):
        assert rank.timeline(EARLY, "a.md", descending=descending) < rank.timeline(
            EARLY, "b.md", descending=descending
        )


@pytest.mark.parametrize("descending", [True, False])
def test_a_cursor_reads_back_the_moment_it_was_written_for(descending):
    raw = rank.timeline(LATE, "合同/a.md", descending=descending)
    spot = rank.read_timeline(raw, descending=descending)
    assert spot.at == LATE
    assert spot.tie == "合同/a.md"


def test_reading_a_cursor_the_wrong_way_round_gives_a_different_moment():
    """方向读错了就会得到另一个时刻——所以接口按自己那档的方向去读。"""
    raw = rank.timeline(LATE, "a.md", descending=True)
    assert rank.read_timeline(raw, descending=True).at == LATE
    assert rank.read_timeline(raw, descending=False).at != LATE


def test_a_cursor_that_is_not_one_is_refused():
    with pytest.raises(UnprocessableEntityError):
        rank.read_timeline("", descending=True)


def test_two_documents_with_the_same_title_still_have_an_order():
    first, second = uuid.uuid4(), uuid.uuid4()
    a = rank.title_tie("周报", first)
    b = rank.title_tie("周报", second)
    assert a != b
    assert (a < b) == (str(first) < str(second))


def test_a_documents_tie_cannot_collide_with_a_file_name():
    """分隔符是文件名字里不会出现的字节：文档和文件的最后一段不会撞在一起。"""
    document = rank.title_tie("预算表", uuid.uuid4())
    assert chr(1) in document
    assert "/" not in document.split(chr(1))[0]


def test_the_sort_values_the_page_can_ask_for():
    assert rank.SORTS == ("off", "asc", "desc")
