"""`/admin/subscriptions` 的请求体。

只管形状：provider 的字面量集合、label 的长度、定向重授权的目标 id。业务判断
（这个目标存不存在、是不是同身份）都要读库，在服务层。
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class DeviceFlowStart(BaseModel):
    """`POST /admin/subscriptions/device-flows` 的体 —— 开启一次导入或定向重授权。

    ``target_subscription_id`` 非空时是**定向重授权**：服务端完成时会校验回来的
    账号与旧行是同一身份（同一 `sub` 与同一 `chatgpt_account_id`），不同则 400
    —— 防止把别人的订阅接到这条线上（cc-switch `add_account_internal` 同规）。
    """

    provider: Literal["openai_codex"] = "openai_codex"
    label: str | None = Field(default=None, max_length=200)
    target_subscription_id: uuid.UUID | None = None
    # 显式指定上游模型（如 openai/gpt-5.6-luna）；空 = 跟随部署默认
    # （settings.subscription_upstream_model），行里存 NULL，热配保留。
    # 定向重授权时空 = **继承旧行的选择**（重授权换凭据不换配置，服务层兑现）。
    upstream_model: str | None = Field(default=None, min_length=1, max_length=200)


class UpstreamModelUpdate(BaseModel):
    """`PATCH /admin/subscriptions/{id}/upstream-model` 的体 —— 授权完成后改上游模型。

    字段**必填但可为 null**（BudgetUpdate 同规）：缺省（`{}`）直接 400 ——
    「忘了传」绝不能被读成「清除选择」；``None`` 才是显式的清除，回落到部署
    默认 —— 网关那一下推的是回落后的解析值，不是 NULL（网关的合并语义里
    缺省才是「不动」）。
    """

    upstream_model: str | None = Field(min_length=1, max_length=200)
