"""浏览器推送的订阅：一台设备上的一个浏览器答应接收推送。

一行就是一次 `pushManager.subscribe()` 的结果 —— 浏览器向它自己的推送服务商
（Chrome 走 FCM，Firefox 走 autopush）换来的一个投递地址加两把加密材料。三样都得
存下来：`endpoint` 是往哪儿发，`p256dh` 和 `auth` 是把内容加密成只有这个浏览器能
解开的样子。**服务端看不到明文也解不开**，这是 Web Push 的设定，不是我们的选择。

一个人有几个浏览器就有几行，所以主键不是 user_id 而是 `endpoint`：同一个浏览器重
复订阅拿回同一个 endpoint，按它去重，不会越攒越多。

订阅会自己失效 —— 用户在浏览器设置里撤掉权限、清了站点数据、换了设备。失效的表现
是推送服务商回 404 或 410，那时这一行就该删掉；继续留着只会每轮都白发一次。
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class PushSubscription(Base):
    __tablename__ = "push_subscription"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 谁的浏览器。人被删掉时订阅跟着走 —— 一个投不到任何人身上的投递地址没有意义。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: 推送服务商给的投递地址。唯一：同一个浏览器重复订阅换回同一个值。
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    #: 这个浏览器的公钥，内容用它加密。
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 加密用的认证密钥（`auth` secret）。
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 订阅时的 User-Agent，纯给人看 —— 「我有哪几个浏览器开了推送」只能靠它认。
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    #: 最后一次成功投递。判断一个订阅是不是早就没人用了，看它。
    last_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
