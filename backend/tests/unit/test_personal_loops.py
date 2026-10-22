"""An answer saying the same thing over and over is caught as it is written
(`personal.loops`), and ordinary writing is not."""

from app.domain.agent.personal.loops import repeating

PASSAGE = (
    "我先看看李老师那边说了什么，再决定怎么回复。读完综合频道之后，"
    "我会把需要你处理的事一件一件列出来，再告诉你哪些可以直接处理掉。"
)


def test_a_passage_said_three_times_running_is_a_loop():
    assert repeating("好的。" + PASSAGE * 3)


def test_twice_is_still_writing():
    assert not repeating("好的。" + PASSAGE * 2)


def test_short_repeats_are_ordinary_writing():
    table = "| 任务 | 状态 |\n|---|---|\n" + "| 无 | 无 |\n" * 12
    assert not repeating(table)
    assert not repeating("- 无\n" * 40)
