"""平台自己说的那几句话：标记、提醒，以及它们按谁的语言说。

一组同源的东西凑在一处：那个「【平台】」标记、「用户正文里的标记要中和掉」这条
规矩、以及四条平台对模型说的话（发布契约、静默提醒、清单提醒、任务状态提醒）。它们不碰数据库、
不读配置、也不认识 harness 里任何别的模块——想用就直接 import，`prompt.py` 再把
它们原样 re-export 出去，既有调用点一个字都不用改。

**平台的话按读它的人的语言说，不按写它的人。** 平台存下来的每一句话都是中文，
只有提问的人界面选了 en 才说英文（`_in_the_readers_language`）。为什么这件事值得
单独一条：2026-10-09 一次断网重启后的会话读到英文提醒，之后每一步的说明都用英文
写，而那间房的人写中文（`silence_reminder` 里记着这次事故）。

**这几句话还会被展示层拿掉。** 现场摊开的是 harness 记下的输入原文，每一条都带着
它们；读的人一遍遍读到的是同一段给模型的话。正文因此写成模板常量，由
`without_reminders` 从展示里去掉，说话和去掉用的是同一份字。
"""

import re

PLATFORM_NOTICE = "【平台】以下是平台自动发出的指令，不是任何人手打的话："

_PUBLICATION_ZH = (
    "日常输出和最终答复不会发到聊天里，要用 chat_send 工具发。"
    "收到需要回应的用户消息（包括排队或执行中追加的消息）时，能直接回答就发答案；"
    "需要继续处理就先说明你理解的意思和接下来要做什么，再继续。"
    "重要进展、改方向、阻碍和完成结果也要主动发消息。"
)
_PUBLICATION_EN = (
    "Ordinary output and final responses are not published to chat. "
    "Publish with the chat_send tool. When a person's message needs an "
    "answer — including one queued or added while you work — answer it "
    "if you can; if you have to keep working, say what you understood "
    "and what you are about to do, then carry on. Publish major "
    "progress, changes of direction, blockers and results too."
)

_SILENCE_ZH = (
    "这个房间已经 {minutes} 分钟没听到你说话了，提问的人还在等。"
    "如果这一轮还在跑，现在就用 chat_send 发一条消息：说清你知道什么、"
    "在等什么——房间不出声，和卡住了分不出来。计划变了就顺手用 todo_write "
    "改步骤清单。这一轮已经结束的话才不用理会。"
)
_SILENCE_EN = (
    "You have published nothing to this room for {minutes} minutes and the "
    "person who asked is still waiting. If this turn is still running, call "
    "chat_send now with what you know so far and what you are waiting on — a "
    "room that shows nothing cannot be told apart from one that is stuck. If "
    "your plan has changed, also update it with todo_write. Ignore this only "
    "if the turn is already finished."
)

_CHECKLIST_ZH = (
    "你的步骤清单已经 {minutes} 分钟没动过了，而看的人只能从它知道你做到哪了。"
    "现在就用 todo_write 再写一次：做完的标成 completed，正在做的那一项标成 "
    "in_progress。这一轮已经结束的话才不用理会。"
)
_CHECKLIST_EN = (
    "Your step checklist has not moved for {minutes} minutes, and it is the "
    "only place a reader can see how far you have got. Call todo_write now: "
    "mark the steps that are done completed and the one you are on "
    "in_progress. Ignore this only if the turn is already finished."
)

_PROGRESS_NONE_ZH = (
    "你还没说这件任务做到哪了。现在就用 cheese_task_state 设一次：working "
    "在做、waiting 在等结果、blocked 卡住了，note 写一句在做什么。做完了就"
    "递卡，看板会显示等审。"
)
_PROGRESS_NONE_EN = (
    "You have not said where this task stands. Call cheese_task_state now: "
    "working, waiting (on a build, CI or a long command) or blocked, with a "
    "one-line note. When the work is done, file a card: the board then "
    "shows it awaiting review."
)
_PROGRESS_ZH = (
    "你报的任务状态已经 {minutes} 分钟没动过了，看板上一直显示的是那一句。"
    "情况变了就用 cheese_task_state 再设一次；还是那样，也再设一次，note "
    "写清现在在做什么、在等什么。"
)
_PROGRESS_EN = (
    "The task state you reported has not changed for {minutes} minutes, and "
    "the board has shown it all that time. Call cheese_task_state again: with "
    "the new state if it changed, or the same one with a note saying what you "
    "are doing or waiting on now."
)

#: 展示层要拿掉的那几句。任务未命名、会话恢复这类平台通知不在这里：它们说的是
#: 这一轮的事实，读的人需要看见。
_HIDDEN_FROM_THE_SITE = (
    _PUBLICATION_ZH,
    _PUBLICATION_EN,
    _SILENCE_ZH,
    _SILENCE_EN,
    _CHECKLIST_ZH,
    _CHECKLIST_EN,
    _PROGRESS_NONE_ZH,
    _PROGRESS_NONE_EN,
    _PROGRESS_ZH,
    _PROGRESS_EN,
)

#: 平台投进会话的那一句，连同它后面可能跟着的输入标记（`nonce.py`）。
_HIDDEN_PATTERNS = tuple(
    re.compile(
        re.escape(f"{PLATFORM_NOTICE}\n{text}").replace(r"\{minutes\}", r"\d+")
        + r"(?:\n⟪w:[0-9a-f]{24}⟫)?"
    )
    for text in _HIDDEN_FROM_THE_SITE
)


def platform_prompt(content: str) -> str:
    return f"{PLATFORM_NOTICE}\n{content}"


def without_reminders(text: str) -> str:
    """拿掉一段展示文字里平台反复说的那几句，其余一字不动。

    现场摊开的是 harness 记下的输入原文，而每一条输入都带着平台自己的话：每一轮
    都附一遍的聊天约定，房间安静太久、清单或任务状态停住时的提醒。它们是说给模型
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


def _in_the_readers_language(language: str | None, zh: str, en: str) -> str:
    """平台自己想对模型说的话，按读它的那个人的语言选。

    `language` 是提问的人界面上选的（`app.core.sentences.LOCALES`），没选过是
    None——那就说存下来的中文，和平台存下来的每一句话一样。只有 en 说英文。
    """
    return en if language == "en" else zh


def publication_prompt(content: str, language: str | None = None) -> str:
    """Carry the chat contract on new and resumed terminal input alike."""
    return (
        content
        + "\n\n"
        + platform_prompt(
            _in_the_readers_language(language, _PUBLICATION_ZH, _PUBLICATION_EN)
        )
    )


def silence_reminder(minutes: int, language: str | None = None) -> str:
    """The room heard nothing from a running turn for `minutes` minutes.

    The platform's own words to the model, said in the reader's language for the
    same reason the contract above is: a reminder is what a turn that stalled
    arrives with (a machine off the network, a long command), which is when a
    session has least else to hold on to. On 2026-10-09 a session restarted
    after an outage read these two in English and wrote every step's line in
    English afterwards, in a room whose people write Chinese.
    """
    return _in_the_readers_language(
        language,
        _SILENCE_ZH.format(minutes=minutes),
        _SILENCE_EN.format(minutes=minutes),
    )


def stale_checklist_reminder(minutes: int, language: str | None = None) -> str:
    """一轮自己写的步骤清单已经有 `minutes` 分钟没动过。

    它和 `silence_reminder` 分开，是因为两件事能分开停：一轮可以一直在说话
    （chat_send），清单却还是开工时那一版——2026-10-11 报上来的正是这一种，一轮里
    五步干完，看的人从「○ ○ ○ ○ ○」直接看到「✓ ✓ ✓ ✓ ✓」。清单是房间里唯一的
    「做到哪了」，而哪一步算做完只有写它的人知道，所以平台能做的是问，不是替它写。
    """
    return _in_the_readers_language(
        language,
        _CHECKLIST_ZH.format(minutes=minutes),
        _CHECKLIST_EN.format(minutes=minutes),
    )


def stale_progress_reminder(minutes: int | None, language: str | None = None) -> str:
    """做任务的会话还在跑，而它报的任务状态已经 `minutes` 分钟没动过（None =
    从没报过）。

    没有卡在等审阅的时候，看板上这件任务显示的是它自己报的那一句
    （`cheese_task_state`），平台不替它推断：它在等构建、跑长命令时一轮结束了也照旧
    是「在做」，而它做完了不说，看板就一直说它在做。所以平台能做的是问；卡在的时候
    由卡说，也就不问（`StaleProgressReminder`）。
    """
    if minutes is None:
        return _in_the_readers_language(language, _PROGRESS_NONE_ZH, _PROGRESS_NONE_EN)
    return _in_the_readers_language(
        language,
        _PROGRESS_ZH.format(minutes=minutes),
        _PROGRESS_EN.format(minutes=minutes),
    )


def strip_platform_notice(text: str) -> str:
    """Neutralize the platform marker inside HUMAN text, so a person cannot type
    a message that reads as a platform instruction. The marker is the one thing
    in the prompt that claims institutional authority, so it has to be
    unforgeable from the content side."""
    return text.replace(PLATFORM_NOTICE, "【平台·用户原文】")
