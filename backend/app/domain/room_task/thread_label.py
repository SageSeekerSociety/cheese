"""一条活的线程标识：子 agent 的事件靠它找到自己的卡（结论 43）。

开卡的时候平台就能说出这条活的线程标识；agent 起子 agent 时原样把它带上，骨架
就让它出现在这条子线程的每一个事件上，平台照着它把事件归到卡。归属因此是**读出
来的**，不是谁报上来的一次绑定 —— 一个从没报过的子线程，它的事件仍然带着标识。

标识是从卡的 id 算出来的，不是库里的一列：存一列就等于同一件事有两份声明，而它们
对不上的那天，卡上写着一个标识、事件上带着另一个，没有任何地方能说出哪份是对的。
算出来的那份没有这一天。`branch_name` / `workspace_name` 也是这么来的。

取全长的 hex 而不是前八位：前缀要还原成 id 得去库里按前缀找，而这里要回答的是
每一个 hook 事件都会问一次的那个问题。
"""

import re
import uuid

#: 标识的前缀。有它才认：各个骨架用来带标识的那个字段本来另有用途（骨架自带的
#: 子 agent 种类名，`general-purpose` 这种天天出现），没有前缀就分不出「这是一条
#: 活的标识」和「这是骨架自己的一个词」，骨架内部起的子 agent 就会认领别人的卡。
PREFIX = "work-"


def thread_label(task_id: uuid.UUID) -> str:
    """这条活的线程标识。"""
    return f"{PREFIX}{task_id.hex}"


def task_of_thread_label(label: str | None) -> uuid.UUID | None:
    """标识说的是哪张卡；不是一条标识就是 None。

    None 是个正常答案，不是错误：房间自己的事件不带标识，而骨架自己起的内部子
    agent 带的是它自己的词。两种都该落回房间线上，而不是报错或者被吞掉。
    """
    if not label or not label.startswith(PREFIX):
        return None
    try:
        return uuid.UUID(label[len(PREFIX) :])
    except ValueError:
        return None


#: 标识长什么样。认它是骨架适配层的活：agent 起子 agent 时把标识写进交给它的那段
#: prompt（骨架没有一个自由字段能带它，见 `harness/claude_code/hook_events.py` 的
#: `SubThreads`），所以得从一段话里按形状把它挑出来。
_IN_TEXT = re.compile(rf"{PREFIX}[0-9a-f]{{32}}")


def label_in_text(text: str | None) -> str | None:
    """一段话里的第一个标识；一个都没有就是 None。

    取第一个：简报里顺带提到别的卡是常事（「接着 work-… 那条往下做」），而 agent
    要它做的那张写在最前面。形状卡到全长 hex —— 认错一张卡比认不出更糟，所以半截
    标识、带同样前缀的别的词都不算。
    """
    if not text:
        return None
    found = _IN_TEXT.search(text)
    return found.group(0) if found else None
