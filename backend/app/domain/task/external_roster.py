"""这道题的名单上都有谁，以及拿什么去外站对 —— 外站同步要的最小一份名单。

外站同步要的是一串「报名记录 id → 学号」。这里的学号**就是这个人在平台上的用户名**：
人大的账号走学校单点登录，用户名就是学号（`domain/oauth/services.py` 的 RUC provider
把 ``stno`` 写成 username）；没走单点的学生注册时自己填。

**不读实名信息。** 名单接口上那个 ``queryRealNameInfo`` 占位一直没兑现，原因是实名
数据加密存、只能本人看、精确读还要工单并留痕（`domain/user/realname_services.py`）
—— 拿它当匹配键，等于替这一个功能把实名数据的用途扩一圈。用户名本来就在名单上，够用。

**只收个人报名。** 小队报名的一行是一个队（``is_team``），外站上看不到「这个队做没做」
这件事，所以那种题外站那一列先是空的。

这份名单只给外站同步用，所以它长得不像名单接口那个返回体：只要 id 和用户名，不查
档案、头像、小队，一笔查询就完。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.services import TaskMembershipService
from app.domain.user import services as user_services


async def student_roster(session: AsyncSession, task_id: int) -> list[tuple[int, str]]:
    """``(报名记录 id, 用户名)``，按报名记录 id 排好。"""
    memberships = await TaskMembershipService.of(session).list_memberships_for_task(
        task_id=task_id
    )
    individual = sorted(
        (m for m in memberships if not m.is_team), key=lambda m: m.member_id
    )
    usernames = await user_services.usernames_by_ids(
        session, [m.member_id for m in individual]
    )
    return [(m.member_id, usernames.get(m.member_id, "")) for m in individual]
