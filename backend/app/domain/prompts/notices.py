"""平台自己说的那几句话：标记、提醒，以及它们按谁的语言说。

一组同源的东西凑在一处：那个「【平台】」标记、「用户正文里的标记要中和掉」这条
规矩、以及平台对模型说的几句话（发布契约、没回话的提醒、还在做的提醒、清单提醒、
任务状态提醒）。正文在 `pieces/notices/`。

**平台的话按读它的人的语言说，不按写它的人。** 平台存下来的每一句话都是中文，
只有提问的人界面选了 en 才说英文（`text.in_language`）。为什么这件事值得单独一条：
2026-10-09 一次断网重启后的会话读到英文提醒，之后每一步的说明都用英文写，而那间房
的人写中文。

**这几句话还会被展示层拿掉。** 现场摊开的是 harness 记下的输入原文，每一条都带着
它们；读的人一遍遍读到的是同一段给模型的话。说话和去掉用的是同一份字
（`without_reminders`）。
"""

import re

from app.domain.prompts.text import in_language, raw

PLATFORM_NOTICE = "【平台】以下是平台自动发出的指令，不是任何人手打的话："

#: 展示层要拿掉的那几句，每一句两种语言。任务未命名、会话恢复这类平台通知不在
#: 这里：它们说的是这一轮的事实，读的人需要看见。
_HIDDEN_FROM_THE_SITE = tuple(
    f"notices/{name}.{lang}"
    for name in (
        "publication",
        "silence",
        "still_working",
        "checklist",
        "progress_none",
        "progress",
        "blocked",
    )
    for lang in ("zh", "en")
)


def _hidden_pattern(piece_id: str) -> re.Pattern[str]:
    """平台投进会话的那一句，连同它后面可能跟着的输入标记（`nonce.py`）。"""
    return re.compile(
        re.escape(f"{PLATFORM_NOTICE}\n{raw(piece_id)}").replace(
            re.escape("⟦slot:minutes⟧"), r"\d+"
        )
        + r"(?:\n⟪w:[0-9a-f]{24}⟫)?"
    )


_HIDDEN_PATTERNS = tuple(_hidden_pattern(p) for p in _HIDDEN_FROM_THE_SITE)


def platform_prompt(content: str) -> str:
    return f"{PLATFORM_NOTICE}\n{content}"


def without_reminders(text: str) -> str:
    """拿掉一段展示文字里平台反复说的那几句，其余一字不动。

    现场摊开的是 harness 记下的输入原文，而每一条输入都带着平台自己的话：每一轮
    都附一遍的聊天约定，有人的话没回、太久没说话、清单或任务状态停住时的提醒。它们是说给模型
    听的，不是对话——读者翻现场时只会一遍遍读到同一段。这里只拿掉展示里的那一
    份：日志里一行不少，模型也早就读过了。

    人的原文里出现的标记在进模型前就被换成了「【平台·用户原文】」
    （`strip_platform_notice`），所以按标记切不会误伤谁写的话。
    """
    hidden = text
    for pattern in _HIDDEN_PATTERNS:
        hidden = pattern.sub("", hidden)
    # 没碰上那几句就原样还回去：人的正文里前后的空行不该因为展示被修剪。
    if hidden == text:
        return text
    return re.sub(r"\n{3,}", "\n\n", hidden).strip()


def publication_prompt(content: str, language: str | None = None) -> str:
    """Carry the chat contract on new and resumed terminal input alike."""
    return (
        content + "\n\n" + platform_prompt(in_language("notices/publication", language))
    )


def silence_reminder(minutes: int, language: str | None = None) -> str:
    """A working session read a person's message `minutes` minutes ago and has
    not answered it.

    The platform's own words to the model, said in the reader's language for the
    same reason the contract above is: a reminder is what a turn that stalled
    arrives with (a machine off the network, a long command), which is when a
    session has least else to hold on to. On 2026-10-09 a session restarted
    after an outage read these two in English and wrote every step's line in
    English afterwards, in a room whose people write Chinese.
    """
    return in_language("notices/silence", language, minutes=minutes)


def still_working_reminder(minutes: int, language: str | None = None) -> str:
    """A working session answered the person, then said nothing in the chat for
    `minutes` minutes.

    Its work shows in 现场, but the people in the conversation read a silent
    teammate as a dead one, so a teammate at work says every so often, in one
    short line, what it is on and that it is still on it. The line pings
    nobody: a person having to act is still the only reason to @ someone (the
    chat skill).
    """
    return in_language("notices/still_working", language, minutes=minutes)


def stale_checklist_reminder(minutes: int, language: str | None = None) -> str:
    """一轮自己写的步骤清单已经有 `minutes` 分钟没动过。

    它和 `silence_reminder` 分开，是因为两件事能分开停：一轮可以一直在说话
    （chat_send），清单却还是开工时那一版——2026-10-11 报上来的正是这一种，一轮里
    五步干完，看的人从「○ ○ ○ ○ ○」直接看到「✓ ✓ ✓ ✓ ✓」。清单是房间里唯一的
    「做到哪了」，而哪一步算做完只有写它的人知道，所以平台能做的是问，不是替它写。
    """
    return in_language("notices/checklist", language, minutes=minutes)


def stale_progress_reminder(minutes: int | None, language: str | None = None) -> str:
    """做任务的会话还在跑，而它报的任务状态已经 `minutes` 分钟没动过（None =
    从没报过）。

    没有卡在等审阅的时候，会话停下以后看板上这件任务显示的是它自己报的那一句
    （`cheese_task(action=state)`）：它在等构建、跑长命令时停下了也照旧是「在做」，而它做完了
    不说，看板就一直说它在做。所以平台能做的是问；卡在的时候由卡说，也就不问
    （`StaleProgressReminder`）。
    """
    if minutes is None:
        return in_language("notices/progress_none", language)
    return in_language("notices/progress", language, minutes=minutes)


def blocked_report_reminder(language: str | None = None) -> str:
    """This task's last report was 「卡住了」, and a person's message just opened
    a turn in it.

    The stale-report reminder above (`StaleProgressReminder`) only reaches a
    session that is running, but 「卡住了」 is usually the last thing a turn
    says: by the time someone answers, that turn is over and the reminder can
    no longer go out. So the one moment the teammate is back at work is where
    it is asked, and asked — not decided: whether it can carry on now is its
    own to say, and a person answering has not necessarily unblocked it.
    """
    return in_language("notices/blocked", language)


def strip_platform_notice(text: str) -> str:
    """Neutralize the platform marker inside HUMAN text, so a person cannot type
    a message that reads as a platform instruction. The marker is the one thing
    in the prompt that claims institutional authority, so it has to be
    unforgeable from the content side."""
    return text.replace(PLATFORM_NOTICE, "【平台·用户原文】")
