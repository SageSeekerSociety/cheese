"""接口层的形状，只为 OpenAPI 而存在（路由用 ``**typed_response(Model)`` 声明）。

路由**不拿它们校验**返回值 —— `typed_response` 写的是文档（``response_model=None``），
实际发出去的还是 ``service.py`` 里那几个 ``*_view`` 造的字典。两边一不一致由这两条
路由自己的测试盯着。

字段名跟着**邻居**走，不跟着这个域走：任务侧那两块和 `/tasks/*` 底下别的响应一样用
camelCase，账号那一块和同一张管理页上紧挨着的飞书应用（``feishu_app_view``）一样用
snake_case。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ExternalProblemResultOut(BaseModel):
    """一个领取者在这道题上的结论。``participantId`` 是 ``task_membership.id`` ——
    名单接口发出去的 ``participants[].id`` 就是它，前端按它把这一列贴回名单上。"""

    participantId: int
    studentNo: str
    solved: bool
    acceptedAt: datetime | None


class TaskExternalProblemOut(BaseModel):
    """一道题的外站那一块：题号、上次同步、以及每个领取者的结论。

    ``status`` 是 ``""``（还没同步过）/ ``ok`` / ``failed``，取值在
    ``service.STATUS_OK`` 与 ``STATUS_FAILED``。``error`` 是排查用的原文（``no_account``
    这种），**不是**给人看的句子 —— 界面按 ``status`` 自己说。
    """

    problemNo: str
    source: str
    lastSyncedAt: datetime | None
    status: str
    error: str
    results: list[ExternalProblemResultOut]


class ExternalProblemAccountOut(BaseModel):
    """管理员看的那一行凭据。**没有密码** —— 存进去就再也读不出来。"""

    configured: bool
    source: str
    username: str
    updated_by: str
    updated_at: datetime | None
