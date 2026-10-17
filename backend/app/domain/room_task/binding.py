"""Resolve a work binding, teammate override, or project default against one catalog.

目录（`choices`）是调用方递进来的，本模块不自己去够它：认「这个项目能用哪些模型」
的是 `agent_instance.configuration`（第 5 层），本包在第 4 层，够不着 —— 那份目录
按 id 从 `catalog` 拿，本模块只在它上面挑一个。别名表（目录 id 和上游名互为表里的
地方）在 `agent.market`，也是第 5 层，所以那一行 `wire` 也跟着目录一起递下来。
"""

from dataclasses import dataclass

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.room_task.models import Task


@dataclass(frozen=True, slots=True)
class WorkBinding:
    """一条活占用的模型资源。

    ``supply`` 跟着一起出，因为它不是从 id 上猜出来的 —— 订阅模型的 id 是
    ``sonnet`` 这样的短名，前缀里看不出它走订阅；目录
    （``agent_instance.configuration.catalog``）是唯一知道这件事的地方，而调用方
    都要它：轮次组装拿它决定要不要换成 Claude 的全名，准入拿它决定送哪个池。

    ``wire_model`` 真发到上游、也真记进 `usage`，同样从目录上取下来（那一行的
    ``wire``），不在这里现算：算它要知道 id 和上游名互为表里，那是别名表的事。
    """

    model: str
    supply: str
    wire_model: str
    effort: str | None = None


def resolve(
    task: Task | None,
    choices: dict[str, dict],
    *,
    agent_model: str | None = None,
    default_model: str | None = None,
) -> WorkBinding:
    """Explicit work, then teammate, then supplied default, then project main model."""
    bound = (task.model or "").strip() if task is not None else ""
    bound = bound or agent_model or default_model or ""
    effort = task.effort if task is not None else None
    if not bound:
        default = next(
            (choice for choice in choices.values() if choice["default"]), None
        )
        if default is None:
            raise ValidationError(say("noDefaultModel"))
        return WorkBinding(
            model=default["id"],
            supply=default["supply"],
            wire_model=default["wire"],
            effort=effort,
        )
    chosen = choices.get(bound)
    if chosen is None:
        raise ValidationError(say("taskModelUnavailable", model=repr(bound)))
    return WorkBinding(
        model=chosen["id"],
        supply=chosen["supply"],
        wire_model=chosen["wire"],
        effort=effort,
    )


def catalog_id(spent: str, choices: dict[str, dict]) -> str | None:
    """真发出去的那个模型名 → 目录里的 id；目录认不出来就 `None`。

    目录和用量行说的不是同一套词：目录里订阅模型的 id 是 `sonnet` 这样的短名，
    真发到上游、也真记进 `usage` 的是 `claude-sonnet-5`。卡上一分钱没花时显示
    「用哪个模型」要从绑定算（短名），花过之后要从用量算（全名），两头直接吐出来
    就是同一张卡、同一个模型，在第一次请求之后换了个名字 —— 用户读到的是「模型
    被换了」。

    这一反查对的是目录每一行上的 `wire`（`agent_instance.configuration.catalog`
    算好的那一个名字，网关模型就是它自己的 id），所以本包不用再问一次别名表。

    认不出来就 `None`，由调用方照原样显示：上游给回一个目录里没有的名字（换了
    代次、带上了日期），显示它真花在谁身上，仍然比显示一个猜出来的短名诚实。
    """
    if spent in choices:
        return spent
    for choice in choices.values():
        if choice.get("wire") == spent:
            return choice["id"]
    return None
