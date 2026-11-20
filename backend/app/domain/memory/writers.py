"""改记忆的路有哪几条（`memory_file_changes.writer`）。

单独一个模块、没有任何依赖：存取层按它记、路由按它点名自己，而路由不读模型
（架构守卫 C2）。
"""

import enum


class MemoryWriter(enum.StrEnum):
    """哪一种写入方改了一条记忆。

    `changed_by` 回答「是谁」（一个 handle），这一列回答「从哪条路来的」：同一个
    芝士的 handle 既可能是一轮对话收回来的改动，也可能是整理；同一个人的 handle
    既可能是在面板上改的，也可能是从一条改动记录恢复的。撤销一次改动之前，人要
    知道的正是这一层。
    """

    #: 一轮对话结束（或开场）时，会话里改过的记忆对账收回来（`session.apply_tree`）。
    turn = "turn"
    #: 整理（dream）里 team 那一轮。
    dream_team = "dream_team"
    #: 整理（dream）里某个人的 private 那一轮。
    dream_private = "dream_private"
    #: 一个人在记忆面板或记忆接口上写、删。
    panel = "panel"
    #: 一个人从一条改动记录把那条记忆恢复成改动前的样子（`history.restore`）。
    restore = "restore"
    #: 一个项目方法被确认保存，它并进去的那几条 team 记忆随之删掉。
    skill = "skill"
    #: 旧记忆表一次性搬进这棵树（`migration_service`）。
    migration = "migration"
    #: 一个人自己的芝士改 personal 记忆。personal 不在任何项目里，所以改动记录
    #: 不收它（`files_store._record`）；这一项在，是为了每条写入路都得点名自己。
    assistant = "assistant"
