"""平台自己说的那几句话：标记、提醒，以及它们按谁的语言说。

一组同源的东西凑在一处：那个「【平台】」标记、「用户正文里的标记要中和掉」这条
规矩、以及四条平台对模型说的话（发布契约、没回话的提醒、清单提醒、任务状态提醒）。它们不碰数据库、
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
    "干活的过程不用在对话里播报，现场看得到；只在要人出手、卡住了、递了卡、"
    "改了方向或计划、有改变任务的发现、有重要的结果、做完时主动发消息。"
    "重要的结果是改变大家对这件活的看法或预期的结果：查实了根因、关键的验证有了"
    "结论（通过或失败）、活比预想的大或小。刚动手、正在查、做完一步都不发。"
)
_PUBLICATION_EN = (
    "Ordinary output and final responses are not published to chat. "
    "Publish with the chat_send tool. When a person's message needs an "
    "answer — including one queued or added while you work — answer it "
    "if you can; if you have to keep working, say what you understood "
    "and what you are about to do, then carry on. Do not narrate your "
    "work in the chat: people can watch it live. Publish on your own "
    "only when you need a person, are blocked, filed a card, changed "
    "direction or plan, found something that changes the task, have an "
    "important result, or are done. An important result changes what people "
    "think of the work or expect from it: a root cause confirmed, a key check "
    "came back, passed or failed, the work turning out bigger or smaller than expected. "
    "Starting, checking or finishing one step is not announced."
)

_SILENCE_ZH = (
    "有人对你说的话，你读到 {minutes} 分钟了还没回，他还在等。"
    "现在就用 chat_send 回他：能答就答；还要接着做，就说你理解的意思和下一步。"
    "计划变了就顺手用 todo_write 改步骤清单。你已经停下的话才不用理会。"
)
_SILENCE_EN = (
    "Someone spoke to you {minutes} minutes ago and you have not answered; "
    "they are still waiting. Call chat_send now: answer if you can, or say "
    "what you understood and what you will do next. If your plan has changed, "
    "also update it with todo_write. Ignore this only if the turn is already "
    "finished."
)

_CHECKLIST_ZH = (
    "你的步骤清单已经 {minutes} 分钟没动过了，而看的人只能从它知道你做到哪了。"
    "现在就用 todo_write 再写一次：做完的标成 completed，正在做的那一项标成 "
    "in_progress。你已经停下的话才不用理会。"
)
_CHECKLIST_EN = (
    "Your step checklist has not moved for {minutes} minutes, and it is the "
    "only place a reader can see how far you have got. Call todo_write now: "
    "mark the steps that are done completed and the one you are on "
    "in_progress. Ignore this only if the turn is already finished."
)

_PROGRESS_NONE_ZH = (
    "你还没说这件任务做到哪了。现在就用 cheese_task(action=state) 设一次：working "
    "在做、waiting 在等结果、blocked 卡住了，note 写一句在做什么。做完了就"
    "递卡，看板会显示等审。"
)
_PROGRESS_NONE_EN = (
    "You have not said where this task stands. Call cheese_task(action=state) now: "
    "working, waiting (on a build, CI or a long command) or blocked, with a "
    "one-line note. When the work is done, file a card: the board then "
    "shows it awaiting review."
)
_PROGRESS_ZH = (
    "你报的任务状态已经 {minutes} 分钟没动过了，你停下以后看板显示的就是那一句。"
    "情况变了就用 cheese_task(action=state) 再设一次；还是那样，也再设一次，note "
    "写清现在在做什么、在等什么。"
)
_PROGRESS_EN = (
    "The task state you reported has not changed for {minutes} minutes, and "
    "once you stop, that is what the board shows. Call cheese_task(action=state) again: with "
    "the new state if it changed, or the same one with a note saying what you "
    "are doing or waiting on now."
)
_BLOCKED_ZH = (
    "你上一次给这件任务报的状态是「卡住了，等人帮忙」。现在有人说话、这一轮开了："
    "如果已经能往下走，就用 cheese_task(action=state) 报回 working 或 waiting——不然"
    "你一停下，任务又显示卡住、等负责人来；还卡着就再报一次 blocked，note 写清在等什么。"
)
_BLOCKED_EN = (
    "You last reported this task as blocked, waiting for help. Someone has "
    "spoken and this turn is open: if you can carry on now, call "
    "cheese_task(action=state) again with working or waiting — otherwise, once you "
    "stop, the task reads as blocked again and waits on its owner. If you are "
    "still stuck, report blocked "
    "again with a note saying what you are waiting for."
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
    _BLOCKED_ZH,
    _BLOCKED_EN,
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
    都附一遍的聊天约定，有人的话没回、清单或任务状态停住时的提醒。它们是说给模型
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
    """A working session read a person's message `minutes` minutes ago and has
    not answered it.

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

    没有卡在等审阅的时候，会话停下以后看板上这件任务显示的是它自己报的那一句
    （`cheese_task(action=state)`）：它在等构建、跑长命令时停下了也照旧是「在做」，而它做完了
    不说，看板就一直说它在做。所以平台能做的是问；卡在的时候由卡说，也就不问
    （`StaleProgressReminder`）。
    """
    if minutes is None:
        return _in_the_readers_language(language, _PROGRESS_NONE_ZH, _PROGRESS_NONE_EN)
    return _in_the_readers_language(
        language,
        _PROGRESS_ZH.format(minutes=minutes),
        _PROGRESS_EN.format(minutes=minutes),
    )


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
    return _in_the_readers_language(language, _BLOCKED_ZH, _BLOCKED_EN)


def strip_platform_notice(text: str) -> str:
    """Neutralize the platform marker inside HUMAN text, so a person cannot type
    a message that reads as a platform instruction. The marker is the one thing
    in the prompt that claims institutional authority, so it has to be
    unforgeable from the content side."""
    return text.replace(PLATFORM_NOTICE, "【平台·用户原文】")
