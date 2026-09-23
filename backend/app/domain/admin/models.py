"""平台管理员 —— **谁能管这个平台**，不是「谁属于哪个项目」。

和 `ProjectMember` / topic 成员表是两回事：那两张答的是「这个项目里有哪些人」，
而这里答的是「这个后台上能动手的是谁」。所以它是**平台级**的一张表，不挂在任何
项目、话题下面 —— 管理台以后装的不只是反馈（见 `frontend/src/router/feedback.ts`
里 `/admin/*` 那段），一张挂在反馈下面的名单会把它锁死在一个功能里。

两份来源，这里存**第二份**：

* **根管理员**在部署配置里（`settings.platform_admin_handles`）：必填、没填后端起
  不来，因此**页面上删不掉**。
* 页面上加的在这张表里，可以再加回来。

两份分开的理由是单向的：能加人的入口本身也要管理员，所以「一次误操作把名单清空」
没有回头的路 —— 而配置那一份要改回去得有服务器权限，那正是「只有少数人能补救」
该在的位置。判据是两者的**并集**（`services.AdminService.admin_handles`），一个
请求读一次库；谁是从哪一份进来的，页面画得出来：根那批是只读的。

`handle` 而不是 user id：平台里「你是谁」从头到尾是 handle（同一批表里的
`author_handle` / `assignee_handle` / `dismissed_by_handle` 都是），这一层没有一处
挂 user 外键。`added_by_handle` 同样是**快照**而不是外键，理由和
`Feedback.author_handle` 一样：加人的那个人后来注销了，这行仍然要说得出是谁加的。

`UniqueConstraint("handle")` 同时是并发答案：两个人同时加同一个人，数据库拒掉后来
者，写入路径把这次拒绝折成和「重复加」同一个答复（同 `feedback_supports`）。它也是
这张表唯一的索引 —— 每一次读都按 `handle` 找。
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class PlatformAdmin(UuidPk, Base):
    __tablename__ = "platform_admins"
    __table_args__ = (UniqueConstraint("handle", name="uq_platform_admin_handle"),)

    handle: Mapped[str] = mapped_column(String(64))
    added_by_handle: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
