"""平台自己对会话说的话：怎么标记、哪几类会被展示层拿掉、按谁的语言说。

**标记。** 平台说的每一段都包在 ``<platform_notice type="…">…</platform_notice>``
里，``type`` 说它是哪一类（`NoticeType`）。人写的正文里出现的标记在进模型前被中和
（`strip_platform_notice`），所以只有平台写得出一个真的标记。

**平台的话按读它的人的语言说，不按写它的人。** 平台存下来的每一句话都是中文，
只有提问的人界面选了 en 才说英文（`text.in_language`）。2026-10-09 一次断网重启后
的会话读到英文提醒，之后每一步的说明都用英文写，而那间房的人写中文。

**展示层按类型拿掉一部分。** 现场摊开的是 harness 记下的输入原文，每一条都带着
每轮附上的约定和提醒；读的人一遍遍读到的是同一段给模型的话。拿掉的是
`HIDDEN_TYPES` 这几类；这套标记之前存下的输入用的是老的「【平台】」写法，它们的原文
登记在 `legacy/` 里，照旧拿掉，不迁移。
"""

import re
from enum import StrEnum
from functools import cache
from pathlib import Path

from app.domain.prompts.text import in_language


class NoticeType(StrEnum):
    """What a platform notice is, as its tag says."""

    #: What a kind of session is told after every message (`render_notice`).
    contract = "contract"
    #: A reminder: no answer yet, quiet too long, a checklist or a state not moved.
    reminder = "reminder"
    #: Where the session is and how the project stands (the opening, what changed).
    state = "state"
    #: Something changed under a running session (a document edited, a file saved).
    change = "change"
    #: Work the platform hands the session (a returned card, a routine's run).
    instruction = "instruction"
    #: True of this turn only (unnamed task, a continued turn, credits running low).
    turn = "turn"
    #: Raised by the harness itself (a background job finished).
    harness = "harness"


#: The types the site leaves out of what it shows of a session's input.
HIDDEN_TYPES = frozenset({NoticeType.contract, NoticeType.reminder})


def platform_prompt(content: str, kind: NoticeType) -> str:
    """``content`` as the platform says it, tagged with what it is."""
    return f'<platform_notice type="{kind}">\n{content}\n</platform_notice>'


def notice_template(kind: NoticeType) -> str:
    """A notice with ``⟦slot:text⟧`` for its content, for a harness that raises
    notices of its own (`SessionSpec.notice`): one convention, handed over
    rather than restated."""
    return platform_prompt("⟦slot:text⟧", kind)


_TAG = re.compile(r"<(/?)platform_notice")
_HIDDEN = re.compile(
    r'<platform_notice type="(?:'
    + "|".join(sorted(HIDDEN_TYPES))
    + r')">\n.*?\n</platform_notice>(?:\n⟪w:[0-9a-f]{24}⟫)?',
    re.S,
)

#: 这套标记之前平台用的标记。它只出现在之前存下的输入里（`legacy/`）。
LEGACY_MARKER = "【平台】以下是平台自动发出的指令，不是任何人手打的话："
_LEGACY = Path(__file__).parent / "legacy"


@cache
def _legacy_patterns() -> tuple[re.Pattern[str], ...]:
    """The sentences the site hid before notices were tagged, as they were
    said: a minute count is any number, an input nonce may follow."""
    return tuple(
        re.compile(
            re.escape(
                f"{LEGACY_MARKER}\n"
                + path.read_text(encoding="utf-8").removesuffix("\n")
            ).replace(re.escape("⟦slot:minutes⟧"), r"\d+")
            + r"(?:\n⟪w:[0-9a-f]{24}⟫)?"
        )
        for path in sorted(_LEGACY.glob("*.md"))
    )


def without_reminders(text: str) -> str:
    """拿掉一段展示文字里平台反复说的那几类话，其余一字不动。

    它们是说给模型听的，不是对话——读者翻现场时只会一遍遍读到同一段。这里只拿掉
    展示里的那一份：日志里一行不少，模型也早就读过了。人写的标记进模型前就被中和
    了（`strip_platform_notice`），所以按标记切不会误伤谁写的话。
    """
    hidden = _HIDDEN.sub("", text)
    for pattern in _legacy_patterns():
        hidden = pattern.sub("", hidden)
    # 没碰上那几句就原样还回去：人的正文里前后的空行不该因为展示被修剪。
    if hidden == text:
        return text
    return re.sub(r"\n{3,}", "\n\n", hidden).strip()


def silence_reminder(minutes: int, language: str | None = None) -> str:
    """A working session read a person's message `minutes` minutes ago and has
    not answered it.

    Said in the reader's language: a reminder is what a turn that stalled
    arrives with (a machine off the network, a long command), which is when a
    session has least else to hold on to.
    """
    return in_language("notices/silence", language, minutes=minutes)


def still_working_reminder(minutes: int, language: str | None = None) -> str:
    """A working session answered the person, then said nothing in the chat for
    `minutes` minutes.

    Its work shows in 现场, but the people in the conversation read a silent
    teammate as a dead one, so a teammate at work says every so often, in one
    short line, what it is on and that it is still on it.
    """
    return in_language("notices/still_working", language, minutes=minutes)


def stale_checklist_reminder(minutes: int, language: str | None = None) -> str:
    """一轮自己写的步骤清单已经有 `minutes` 分钟没动过。

    它和 `silence_reminder` 分开，是因为两件事能分开停：一轮可以一直在说话
    （chat_send），清单却还是开工时那一版——2026-10-11 报上来的正是这一种。清单是
    唯一的「做到哪了」，而哪一步算做完只有写它的人知道，所以平台能做的是问。
    """
    return in_language("notices/checklist", language, minutes=minutes)


def stale_progress_reminder(minutes: int | None, language: str | None = None) -> str:
    """做任务的会话还在跑，而它报的任务状态已经 `minutes` 分钟没动过（None =
    从没报过）。会话停下以后看板上显示的是它自己报的那一句，所以平台能做的是问。
    """
    if minutes is None:
        return in_language("notices/progress_none", language)
    return in_language("notices/progress", language, minutes=minutes)


def blocked_report_reminder(language: str | None = None) -> str:
    """This task's last report was 「卡住了」, and a person's message just opened
    a turn in it: the one moment the teammate is back at work is where it is
    asked whether it still is — asked, not decided.
    """
    return in_language("notices/blocked", language)


def strip_platform_notice(text: str) -> str:
    """Neutralize the platform's marks inside text a person wrote, so nobody can
    type something that reads as a platform notice: the tag (opening or
    closing — a closing one would end a real notice early) and the marker
    inputs carried before the tag."""
    return _TAG.sub(r"＜\1platform_notice", text).replace(
        LEGACY_MARKER, "【平台·用户原文】"
    )
