"""How wide a task's title may be, counted the way the sidebar runs out.

The sidebar draws a title on one line and cuts the rest with an ellipsis, so
what a title runs out of is pixels, not code points: a 汉字 takes about twice
what a Latin letter does. Counting characters would let an English title be
drawn at twice the length of a Chinese one that passes the same check.
"""

from app.domain.room_task.title_width import (
    TITLE_CHARS,
    TITLE_WIDTH,
    title_fits,
    title_width,
)


def test_a_hanzi_is_two_half_characters():
    assert title_width("任务标题") == 8


def test_latin_letters_digits_and_spaces_are_one_each():
    assert title_width("Fix #3415 now") == len("Fix #3415 now")


def test_wide_punctuation_is_drawn_wide_and_the_ambiguous_kind_is_not():
    # 「，」是全角，和汉字一样占两个半角字符；「·」在侧栏的字体里是窄的。
    assert title_width("，") == 2
    assert title_width("·") == 1


def test_the_limit_a_person_reads_is_half_the_half_characters():
    assert TITLE_WIDTH == TITLE_CHARS * 2


def test_the_limit_is_reached_and_not_passed():
    assert title_fits("一" * TITLE_CHARS)
    assert not title_fits("一" * TITLE_CHARS + "一")
    # 24 个半角字符和 12 个汉字是同一个宽度。
    assert title_fits("a" * TITLE_WIDTH)
    assert not title_fits("a" * (TITLE_WIDTH + 1))


def test_a_mixed_title_is_counted_by_what_it_draws():
    # 「修 bug」＝ 汉字 2 + 空格 1 + 三个字母 3 = 6。
    assert title_width("修 bug") == 6
