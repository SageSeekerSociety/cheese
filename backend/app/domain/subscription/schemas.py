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
