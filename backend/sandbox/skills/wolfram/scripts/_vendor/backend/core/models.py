"""随包的极简数据模型：只留执行通道真正用到的那一个。

上游（AI4S）里还有六元组、策略资产 θ、题库、训练契约等模型，它们服务于
"要模型的自然语言流水线"；那条线不随本 skill 发布，所以不在这里。
"""

from pydantic import BaseModel


class SafetyViolation(BaseModel):
    rule: str
    detail: str
    line: int = 0
