"""谁算平台管理员：一个判据、一份名单、页面上加与删。

`is_admin` 只有这一个实现 —— 反馈的可见性（私密条目谁能看）、管理台的入口、以及
名单自己的读写，问的都是这里同一个答案。判据写两份，症状是「反馈管理进得去、成员
管理进不去」，而两边各自看都「对」。

名单两个来源（`root_admin_handles` 与 `platform_admins` 表）的合并点也在这里。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import BadRequestError, ConflictError, ForbiddenError
from app.domain.admin import repositories as repo
from app.domain.identity.services import IdentityService
from app.domain.user.services import (
    chosen_avatars_by_handle,
    faces_by_handle,
    search_accounts,
    user_by_handle,
    users_by_handle,
)


def root_admin_handles() -> frozenset[str]:
    """部署配置里那份 —— 名单的根。

    读 `settings` 而不是抄成模块常量：测试要把名单换成别人时不重启进程（见
    `tests/integration/test_feedback.py` 的 `as_admin`），而部署侧那道闸门（
    `Settings._require_platform_admins_on_deployment`）保证它不会空。
    """
    return frozenset(settings.platform_admin_handles)


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = repo.AdminRepository(session)
        #: 一个实例（= 一个请求）一次的并集，见 `admin_handles`。
        self._admin_handles: frozenset[str] | None = None

    async def admin_handles(self) -> frozenset[str]:
        """谁算平台管理员：**根 ∪ 页面上加的**。

        两份来源，一个答案。根那半是配置，另半在 `platform_admins` 表里 —— 谁从
        哪一份进来，页面画得出来：根那批只读。

        合成之后**在实例上 memo 一次**：`is_admin` 一次页面渲染要被问十几遍（列表
        的每张卡、详情的每一层楼、每条评论的 `can_delete`），而它现在是一次库读。
        实例是每个请求一个（`get_feedback_service` / `get_admin_service`），所以
        memo 的作用域正好是「这个请求里读一次」，不会跨请求留旧值 —— 有人在页面上
        刚加完一个人，下一个请求就该看见他。
        """
        if self._admin_handles is None:
            self._admin_handles = root_admin_handles() | (
                await self._repo.added_admin_handles()
            )
        return self._admin_handles

    async def is_admin(self, handle: str | None) -> bool:
        return bool(handle) and handle in await self.admin_handles()

    async def require_admin(self, handle: str | None) -> str:
        if not handle:
            raise ForbiddenError("需要登录")
        if not await self.is_admin(handle):
            # 403 here and not 404: /admin/* is documented as existing, so its
            # existence is not a secret — only its contents are.
            raise ForbiddenError("需要平台管理员")
        return handle

    # --- 名单 ---------------------------------------------------------------

    async def admins_out(self) -> dict:
        """名单，分两份给 —— 页面上这两份的**操作权**不一样。

        `root` 来自部署配置：列得出来，删不掉，所以客户端拿到的是一组不能按的
        名字。`added` 是页面上加的：每行带「谁加的、什么时候」，可以删。

        两个列表而不是一个带 `removable` 布尔值的列表：前端要画的是两块不同的
        区域（一块说明白「这几个来自部署配置」，一块带删除按钮），给一个扁平数组
        加个标记，等于让前端自己按标记分组 —— 分组规则就成了第二份判据，哪天服务
        端多一种来源，前端画不出来而且不会报错。

        每一行是这个人的 **handle + 昵称 + 挑过的头像**（`faces_by_handle`：两条
        查询，不分行 N+1）。没有昵称回 null、没挑过头像回 null，**不在这里回退成
        handle**：回退了客户端就分不出「他叫这个」和「他还没起名字」，而这两件事
        在页面上本来就该长得不一样。

        每行还带**账号状态 / 注册时间 / agent 标记**三件，回答「这行权限是不是
        死的」：平台上没有（或已注销）这个账号、这个 handle 是 agent，都是配置里
        写了但永远用不上的权限 —— 三种死权限在页面上各自有形状，所以每一样都是
        一个显式字段，不许客户端靠 `nickname is None` 隐式猜。

        查询预算：每次未缓存读 = 既有 3 条（`list_admins` + `faces_by_handle` 的
        users/profiles）+ 这里的 3 条（`users_by_handle`、`agents_among` 的
        users/bindings），全部批量、走主键/索引，不随名单长度涨。名单长到三位数
        时，这三次 handle→user 翻译可以合并成一次传下去；今天名单是个位数到几十
        行，不为它做。

        要查名字和脸的那批 handle 就取自 `list_admins()` 这一次读的结果，不再为了
        `root` 单独问一遍。

        这张表在一个请求里仍然被读**两次**，但那不是这里造成的：门
        （`PlatformAdminDep` → `require_admin` → `admin_handles`）在 handler 之前
        先读一次，这里是第二次。把 `list_admins()` 的结果回填 memo，是为了让本函数
        返回的 `added` 和同一请求后续再问的 `is_admin` 同源 —— 中间有人并发加人时，
        回给页面的名单与「谁能进来」不会分别来自两个快照（memo 的作用域与理由写在
        `admin_handles`，`add_admin` / `remove_admin` 改完照旧把它置 None）。
        """
        rows = await self._repo.list_admins()
        merged = root_admin_handles() | {row.handle for row in rows}
        # 同一个快照填 memo（root | added）。`add_admin` / `remove_admin` 改完仍然
        # 把它置 None，下一个请求才看得见新值。
        self._admin_handles = merged
        faces = await faces_by_handle(self._session, merged)
        accounts = await users_by_handle(self._session, merged)
        agents = await IdentityService(self._session).agents_among(sorted(merged))

        def face(handle: str) -> dict[str, str | int | bool | None]:
            nickname, avatar_id = faces.get(handle, (None, None))
            user = accounts.get(handle)
            return {
                "handle": handle,
                "nickname": nickname,
                "avatar_id": avatar_id,
                # 平台上没有（或已注销）这个账号 = False。根配置里写错一个名字是允许的，
                # 但那一行是死权限，页面要画得出它和「没设昵称」的区别。
                "has_account": user is not None,
                "registered_at": user.created_at.isoformat() if user else None,
                # agent 不能做任何管理动作（refuse_management_action），所以一个 agent
                # 行也是死权限。只能从根配置混进来（页面加人服务端拒 agent），但混进来
                # 就要看得见。
                "is_agent": handle in agents,
            }

        return {
            "root": [face(h) for h in sorted(root_admin_handles())],
            "added": [
                {
                    **face(row.handle),
                    "added_by_handle": row.added_by_handle,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ],
        }

    async def candidates(self, q: str, *, limit: int = 20) -> list[dict]:
        """「加一个人」那个选择器的候选：按关键词搜账号（handle 或昵称）。

        头像走 `chosen_avatars_by_handle` —— 和反馈卡片、聊天区名册**同一条判据**：
        没挑过头像的人不在这份映射里，界面上画彩色首字母。选择器里画那个全局默认
        头像会让所有人长得一样，比不画更糟。

        已经在名单里的人**照常返回**，带一个 `already_admin`：选择器要把他们画成
        已选中的样子，而不是「搜不到这个人」—— 搜不到说的是另一件事（这个平台上
        根本没有这个账号），两件事在界面上长得像，人就会以为名单已经变了。
        """
        rows = await search_accounts(self._session, q, limit)
        avatars = await chosen_avatars_by_handle(self._session, [h for h, _ in rows])
        admins = await self.admin_handles()
        return [
            {
                "handle": handle,
                "nickname": nickname,
                "avatar_id": avatars.get(handle),
                "already_admin": handle in admins,
            }
            for handle, nickname in rows
        ]

    async def add_admin(self, handle: str, *, by: str) -> bool:
        """把一个人加进名单。返回「这次是真加进去了」还是「他本来就在」。

        三条拒绝，都在这里而不是在路由上（路由拿不到判断权，就不会漏）：

        - **空名字**：名单按 handle 精确匹配，空串谁也匹配不上 —— 那是一个有拼写
          错误的名单，不是一个里面有管理员的名单。同一个理由，配置那道闸门也拦
          空串。
        - **平台里没有这个 handle**：加一个不存在的名字，结果是页面上显示「他在
          名单里」而那个人根本进不来 —— 和配置里写错一个 handle 是同一类错误，
          区别只是这次有地方能当场告诉他。
        - **agent**：管理动作 agent 不能做（`authz.policy.refuse_management_action`），
          所以把 agent 加进管理员名单是加一个永远用不上的权限。这里拒掉，是为了
          让那份名单上的人**都是能真进去的人**。

        他本来就在**根**名单里的话，也是一条拒绝（`ConflictError`）而不是静默成
        功：加进去会在表里落一行删不掉的重复（根删不掉），页面于是会显示两遍。
        """
        wanted = handle.strip()
        if not wanted:
            raise BadRequestError("要加的人不能是空的")
        if wanted in root_admin_handles():
            raise ConflictError(f"{wanted} 是部署配置里的根管理员，不用在页面上加")
        user = await user_by_handle(self._session, wanted)
        if user is None:
            raise BadRequestError(f"平台里没有 handle 是 {wanted} 的账号")
        if await IdentityService(self._session).is_agent(wanted):
            raise BadRequestError(f"{wanted} 是 agent，而管理动作 agent 不能做")
        written = await self._repo.add_admin(wanted, by)
        # 刚加完的人，同一个请求里再问一次 `is_admin` 要能看见他（比如接口返回
        # 新名单）。memo 是请求级的，但请求还没结束。
        self._admin_handles = None
        return written

    async def remove_admin(self, handle: str) -> bool:
        """把一个人移出名单。根管理员删不掉。

        拒绝而不是静默不动：页面上那个删除按钮本来就不该画在根管理员那一行上，
        真按到了说明两边对不上，那就该说出来（同一个理由，前端拿的也是这个答案
        —— 见 `admin_feedback.py` 里「按钮出不出现由服务端说了算」那条口径）。
        """
        wanted = handle.strip()
        if wanted in root_admin_handles():
            raise ConflictError(
                f"{wanted} 是部署配置里的根管理员，页面上删不掉 —— 改配置要有服务器权限"
            )
        removed = await self._repo.remove_admin(wanted)
        self._admin_handles = None
        return removed
