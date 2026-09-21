"""这条活用哪个模型 —— 绑在活上，不绑在参与者上（结论 3、44）。

模型曾经是 agent 的一个属性：想换模型就再建一个 agent。那是把「资源」当成了
「参与者」——同一个人换了把锤子，不会变成另一个人。分参与者的判据是 role，
模型和机器一样，是**一件工作占用的资源**，所以它落在活上：卡是用户接触模型的
唯一地方，卡上并排写着在哪台机器、用哪个模型。

## 次序只有两级，而且第一级可以没有

    这条活自己的绑定  →  项目默认

房间主线没有第一级 —— 它不是一条活，`resolve(None, …)` 拿到的永远是项目默认。
这是特意的（结论 3 第一句）：主线程一轮一换模型，等于每一轮都把 prompt 缓存
打掉，而主线程正是那条最长、最吃缓存的对话。

## 答不出就拒绝，不换池

一条活绑了本项目用不了的模型，这里**报错**，不悄悄退回项目默认（不变量 I27）。
静默换池正是 #325 G2 要杀掉的那种失败：屏幕上写着 Claude，跑的是别的东西，而
谁也不知道什么时候换的。

## 它只答「用哪个模型」

档位策略（超档变成给人的提议）不在这里，在 P38。这里答完就完。
"""

from dataclasses import dataclass

from app.core.errors import ValidationError
from app.domain.agent_instance.configuration import model_choices
from app.domain.room_task.models import Task


@dataclass(frozen=True, slots=True)
class WorkBinding:
    """一条活占用的模型资源。

    ``supply`` 跟着一起出，因为它不是从 id 上猜出来的 —— 订阅模型的 id 是
    ``sonnet`` 这样的短名，前缀里看不出它走订阅；目录（``model_choices``）是
    唯一知道这件事的地方，而调用方三个都要它：轮次组装拿它决定要不要换成
    Claude 的全名，准入拿它决定送哪个池。
    """

    model: str
    supply: str
    effort: str | None = None


def resolve(task: Task | None, project_settings: dict | None) -> WorkBinding:
    """这一轮用哪个模型。``task=None`` 是房间主线，它永远走项目默认。"""
    catalog = {choice["id"]: choice for choice in model_choices(project_settings)}
    bound = (task.model or "").strip() if task is not None else ""
    effort = task.effort if task is not None else None
    if not bound:
        default = next(
            (choice for choice in catalog.values() if choice["default"]), None
        )
        if default is None:
            raise ValidationError("当前项目没有可用的默认模型，请检查模型服务")
        return WorkBinding(model=default["id"], supply=default["supply"], effort=effort)
    chosen = catalog.get(bound)
    if chosen is None:
        raise ValidationError(f"这条活绑的模型 {bound!r} 在当前项目里用不了，请改绑")
    return WorkBinding(model=chosen["id"], supply=chosen["supply"], effort=effort)
