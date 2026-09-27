"""注册凭据由平台管理员发放 —— `/users/invite-codes` 那三条不是「登录即可」。

`settings.require_invite_code` 打开时（受支持的部署配置），一个邀请码就是一张注册
门票：`/users/auth/email-code`、`/users/oauth/create` 都要拿它换一个账号
（`test_oauth_invite_code.py` 钉的就是那两条路）。所以「谁能建码」等于「谁能发
账号」，「谁能停用」等于「谁能在注册窗口上挂锁」—— 判据是**平台管理员**
（`admin_common.PlatformAdminDep`：部署配置里的根名单 ∪ `platform_admins` 表），
不是「有一个登录会话」。

三条路由的 summary 都写着 `(admin)`，函数体里却只有 `require_auth_user`。这个文件
钉的是**修完之后浏览器会收到什么**，不看实现：

1. 登录了的陌生人建码 → 403，且库里没多出一行；
2. 登录了的陌生人停用 → 403，且那个码之后仍然活着（`is_active` 还是 True，注册
   通道没被关上）；
3. 匿名 → 401，一个码也建不出来；
4. 平台管理员两条都做得到 —— 否则修的是「谁都进不去」，不是「只有管理员进得去」。

为什么是 403 而不是 404：这个路径的存在不是秘密（summary 里就写着 admin，同一个
前缀下还有登录、注册一大片公开路由），要拒的是「有身份但不够格」，403 说的正是
这件事。404 会把「你不够格」和「这儿没东西」混成一句，前端也就分不出该弹登录还是
弹权限。至于**匿名**是 401 还是 403：`/users/*` 这一族的写路由都是
`require_auth_user` 的 401（`test_questions.py::test_cancel_invitation_no_auth`），
这里跟这一族走。

`GET /users/invite-codes`（列表）**今天打不到**：`GET /users/{userId}`（`users.py`
里更早注册）先接住 `invite-codes`，落到 int 转换上回 400。它也被补上了同一道门
—— 万一有人把路由顺序改回来，它一出生就是管理员专有的 —— 但那件事属于路由顺序，
不在这个文件的断言里（见 `test_the_list_route_is_unreachable_today`）。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.invite.models import InviteCode
from app.domain.invite.services import InviteCodeService
from tests.integration.conftest import CreatedUser, UserCreator

#: 一个只在名单里、也不是任何东西成员的人 —— 平台管理员是平台级的事实。
STRANGER = "ic-stranger"


def _headers(user: CreatedUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def stranger(user_client: UserCreator, api_client: TestClient) -> CreatedUser:
    """一个真的登录了的普通人：有账号、有会话、没有任何管理权。"""
    user = user_client.create_user(username=STRANGER)
    user.token = user_client.login(api_client, user.username, user.password)
    return user


@pytest.fixture
def as_admin(
    user_client: UserCreator, api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> CreatedUser:
    """把 ``ADMIN`` 放进部署配置那份根名单，并给他一个真会话。

    `AdminService.admin_handles` 每次请求都重读 `settings`（见它的 docstring），
    所以换名单不用重启进程 —— 同 `test_admin_members.py` 的 `as_admin`。
    这里比那边多一步登录：这几条路由既要求管理员、又要求「一个登录会话」
    （`created_by` 记的是 user id），所以操作者得是个真账号。
    """
    user = user_client.create_user(username="ic-admin")
    user.token = user_client.login(api_client, user.username, user.password)
    monkeypatch.setattr(settings, "platform_admin_handles", [user.username])
    return user


def _mint(client: TestClient, headers: dict[str, str] | None = None):
    return client.post(
        "/users/invite-codes",
        json={"maxUses": 3},
        headers=headers or {},
    )


def _deactivate(
    client: TestClient, code_id: int, headers: dict[str, str] | None = None
):
    return client.delete(f"/users/invite-codes/{code_id}", headers=headers or {})


def _rows(db_session: AsyncSession, portal) -> list[InviteCode]:
    """库里现在的码，按 id 排 —— 断言「有没有多出一行」用。"""

    async def _read() -> list[InviteCode]:
        result = await db_session.execute(select(InviteCode).order_by(InviteCode.id))
        return list(result.scalars().all())

    return portal.call(_read)


def _an_existing_code(db_session: AsyncSession, portal) -> InviteCode:
    """一个已经发出去的码。直接走服务层造，不经过要测的那条路由。"""

    async def _make() -> InviteCode:
        code = await InviteCodeService(db_session).create_code(max_uses=1, created_by=1)
        await db_session.flush()
        return code

    return portal.call(_make)


def _is_active(db_session: AsyncSession, portal, code_id: int) -> bool:
    async def _read() -> bool:
        row = (
            await db_session.execute(select(InviteCode).where(InviteCode.id == code_id))
        ).scalar_one()
        return row.is_active

    return portal.call(_read)


# --- 建码 -------------------------------------------------------------------


def test_a_logged_in_stranger_cannot_mint_an_invite_code(
    api_client: TestClient,
    stranger: CreatedUser,
    db_session: AsyncSession,
    _portal,
):
    """有会话不等于有资格：陌生人建码被拒，而且库里不多一行。

    两件事一起断：状态码说的可能是「拒绝了」而库里已经写进去了（403 之后
    `session.commit()` 没跑到，但写法上很容易反过来）。
    """
    before = [row.id for row in _rows(db_session, _portal)]

    response = _mint(api_client, _headers(stranger))

    assert response.status_code == 403, response.text
    assert [row.id for row in _rows(db_session, _portal)] == before


def test_an_anonymous_caller_cannot_mint_an_invite_code(
    api_client: TestClient,
    db_session: AsyncSession,
    _portal,
):
    """连会话都没有的人：401（`/users/*` 这一族的写路由口径），一个码也不多。

    401 而不是 403：这里没有可以被拒的身份，缺的是凭据本身 —— 和
    `test_cancel_invitation_no_auth` 对同一件事的回答一样。
    """
    before = [row.id for row in _rows(db_session, _portal)]

    response = _mint(api_client)

    assert response.status_code == 401, response.text
    assert [row.id for row in _rows(db_session, _portal)] == before


def test_the_platform_admin_can_mint_an_invite_code(
    api_client: TestClient, as_admin: CreatedUser
):
    """对照组：管理员建得出来，回的是那个码本身。

    断言的是**信封里的 `code`**（201）而不是 HTTP 状态码：这条路由历来就是
    「HTTP 200 + `{"code":201}`」（审计的原始输出也是这个形状），前端 `request()`
    解包靠的就是信封 —— 顺手把 HTTP 码改成 201 会是一次没人要的契约改动。
    """
    response = _mint(api_client, _headers(as_admin))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 201
    assert body["data"]["code"]
    assert body["data"]["maxUses"] == 3


# --- 停用 -------------------------------------------------------------------


def test_a_logged_in_stranger_cannot_deactivate_an_invite_code(
    api_client: TestClient,
    stranger: CreatedUser,
    db_session: AsyncSession,
    _portal,
):
    """停用是「关上别人的注册通道」：陌生人被拒，且那个码**还活着**。

    「还活着」这半才是这条用例的重点 —— 回 403 但已经把 `is_active` 改成 False 的
    写法，只断言状态码是看不出来的。
    """
    code = _an_existing_code(db_session, _portal)

    response = _deactivate(api_client, code.id, _headers(stranger))

    assert response.status_code == 403, response.text
    assert _is_active(db_session, _portal, code.id) is True


def test_an_anonymous_caller_cannot_deactivate_an_invite_code(
    api_client: TestClient, db_session: AsyncSession, _portal
):
    code = _an_existing_code(db_session, _portal)

    response = _deactivate(api_client, code.id)

    assert response.status_code == 401, response.text
    assert _is_active(db_session, _portal, code.id) is True


def test_the_platform_admin_can_deactivate_an_invite_code(
    api_client: TestClient,
    as_admin: CreatedUser,
    db_session: AsyncSession,
    _portal,
):
    """对照组：管理员停得掉，库里那一行真的翻过去了。"""
    code = _an_existing_code(db_session, _portal)

    response = _deactivate(api_client, code.id, _headers(as_admin))

    assert response.status_code == 200, response.text
    assert _is_active(db_session, _portal, code.id) is False


# --- 列表那条打不到 ---------------------------------------------------------


def test_the_list_route_is_unreachable_today(
    api_client: TestClient, as_admin: CreatedUser, db_session: AsyncSession, _portal
):
    """`GET /users/invite-codes` 今天被 `GET /users/{userId}` 挡住，谁调都是 400。

    这条用例记的是一个**已知的坏形状**，不是想要的行为：`invite-codes` 落到
    `{userId}` 的 int 转换上，回的是 `int_parsing`。它记在这儿，是因为
    「邀请码的值能不能被任意登录用户读到」这个问题的答案就藏在这条路由能不能被
    命中里 —— 今天不能，所以那半条不成立。

    哪天有人把路由顺序改对（或者把这条挪到 `/{userId}` 前面），这条会变红：那不是
    回归，是提醒 —— 改对之后 `GET` 必须仍然是管理员专有的（路由上已经挂了
    `PlatformAdminDep`），把这个断言改成「管理员 200 / 陌生人 403」再提交。
    """
    code = _an_existing_code(db_session, _portal)

    response = api_client.get("/users/invite-codes", headers=_headers(as_admin))

    assert response.status_code == 400, response.text
    # 400 的 body 是校验错误的信封，它会把**路径**原样抄回来（"input":"invite-codes"），
    # 所以不能断言「body 里没有 invite 这个词」—— 要断言的是没有**码的值**。
    assert code.code not in response.text
