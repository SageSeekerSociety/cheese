"""平台管理员名单：昵称与脸、条件请求、以及「删得掉的只有页面加的那些」。

接口是 `admin_members.py`（`/admin/admins` 那三条 + `/admin/users`），服务是
`AdminService`，门是 `PlatformAdminDep`。这里钉的是**行为**，不是接口长什么样：
名单每一行带的是不是这个人自己的昵称和脸、304 是不是真的省掉了 body、删一个本来
就不在名单里的人是不是静默成功而不是报错。

头像那一组沿用 `test_feedback.py` 的手法，因为同一条坑在这儿又出现一次：注册的每条
路径都写死 ``default_avatar_id=1``，所以「档案上有个头像 id」**不等于**「这个人挑过
头像」。`_seed_profiles` 自己造 `Avatar` 行，于是「默认头像到底是哪一行」在这个文件
里由测试说了算，而不是一个写死的 1 —— 判据本身是 ``chosen_avatar_ids`` 里那条
``avatar_type != "default"``，这里只是把它喂进去。

「根管理员删不掉」是服务的规则（`AdminService.remove_admin`），所以这里打的是
**路由**：谁被拒由服务端说了算，前端拿到的名单本来就分两份。
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.domain.admin.repositories import AdminRepository
from tests.integration.conftest import session_auth_headers

#: 测试放进管理员名单、用来**操作**的那个 handle。故意不是任何东西的真实成员：
#: 平台管理员是平台级的事实，不是某个项目里的角色。
ADMIN = "am-admin"

STRANGER = "am-stranger"

#: 一个自己挑过头像的人，和一个从没挑过的人。两半缺一不可：只测挑过的，「没挑过
#: 就回 null」这条规则删掉也照样绿。
PICKED = "am-picked"
PLAIN = "am-plain"

#: 「这行权限是不是死的」那一条用例里的三种人：有账号的普通人、有账号的 agent、
#: 平台上根本没账号的（根配置里写错了一个名字）。
HUMAN = "am-human"
BOT = "am-bot"
GHOST = "am-ghost"


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make ``ADMIN`` the platform administrator for one test.

    `AdminService.admin_handles` re-reads `settings` on every request (see its
    docstring), so swapping the allow-list here needs no restart. Copied from
    `test_feedback.py` on purpose: the two admin surfaces answer to the same
    gate, and a second way of opening it would be a second thing to keep true.
    """
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def _roots(monkeypatch: pytest.MonkeyPatch, *handles: str) -> None:
    """把部署配置里的根名单换成这几个。

    比 `as_admin` 多几个根：这几条用例要的不是「谁是操作者」，是「根那一份里有
    好几种人」。后设的赢过夹具设的，所以需要自定义根的用例直接调它、不拿 `as_admin`。
    """
    monkeypatch.setattr(settings, "platform_admin_handles", list(handles))


def _list(client, handle: str) -> dict:
    r = client.get("/admin/admins", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _add(client, handle: str, target: str) -> dict:
    r = client.post(
        "/admin/admins",
        json={"handle": target},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _remove(client, handle: str, target: str) -> dict:
    r = client.delete(f"/admin/admins/{target}", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _seed_profiles(client, picks: dict[str, str]) -> dict[str, int]:
    """给这些 handle 落一份账号 + 档案，返回各自头像素材的 id。

    `picks` 是 handle → ``avatar_type``：``"default"`` 就是注册时人人被写上的那一
    张，``"predefined"`` / ``"upload"`` 才是本人挑的。测试库里没有种子头像
    （``_seed_reference_data`` 只种表情类型），所以这里自己造行 —— 也正因为如此，
    「默认头像到底是哪一行」在这由测试说了算，而不是一个写死的 1。

    账号本身也要造：`admin_members.py` 的 `/admin/users` 和 `AdminService.add_admin`
    都要求这个 handle 在平台上真有一个账号（写错一个名字要被当场拒掉）。
    """
    from app.domain.avatars.models import Avatar
    from app.domain.user.models import User, UserProfile

    ids: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            for handle, avatar_type in picks.items():
                avatar = Avatar(
                    url="",
                    name=f"{avatar_type}.png",
                    avatar_type=avatar_type,
                    created_at=now,
                    usage_count=0,
                )
                s.add(avatar)
                await s.flush()
                ids[handle] = avatar.id
                user = User(
                    username=handle,
                    email=f"{handle}@example.com",
                    created_at=now,
                    updated_at=now,
                )
                s.add(user)
                await s.flush()
                s.add(
                    UserProfile(
                        user_id=user.id,
                        nickname=handle.upper(),
                        intro="",
                        avatar_id=avatar.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await s.commit()

    asyncio.run(_seed())
    return ids


def _seed_agent_binding(client, handle: str) -> None:
    """把一个已有账号的人变成 agent：落一条 platform 绑定。

    名单里的 agent 行走不了页面那一关（`AdminService.add_admin` 拒 agent），所以
    测试里用仓储手插 —— 和生产里「从根配置混进来」是同一种来历：绕过了服务层那道
    拒绝，而这一行仍然要被名单画出来。
    """
    from app.domain.identity.repositories import AgentBindingRepository
    from app.domain.user.repositories import UserRepository

    async def _seed() -> None:
        async with client.test_factory() as s:
            user = await UserRepository(s).get_by_username(handle)
            assert user is not None, f"先造账号再绑 agent：{handle}"
            await AgentBindingRepository(s).add(user_id=user.id)
            await s.commit()

    asyncio.run(_seed())


def _add_roster_row(client, handle: str, *, by: str) -> None:
    """绕过路由直接往 `platform_admins` 写一行 —— 见 `_seed_agent_binding`：agent
    行走不了 POST 那一关，而「两组同一个行形状」要在 added 这半也钉一遍。"""

    async def _add() -> None:
        async with client.test_factory() as s:
            assert await AdminRepository(s).add_admin(handle, added_by=by)
            await s.commit()

    asyncio.run(_add())


def _seed_bare_account(client, handle: str) -> None:
    """只落一个 `User` 行、**故意不造 profile**：昵称和头像保持 null，于是下一次
    GET 里这一行变的只有 `has_account` / `registered_at` —— ETag 翻了，功劳就赖
    不到既有字段头上，证明增强字段进了 ETag 输入。"""
    from app.domain.user.models import User

    async def _seed() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            s.add(
                User(
                    username=handle,
                    email=f"{handle}@example.com",
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.commit()

    asyncio.run(_seed())


@contextmanager
def _counting_sql() -> Iterator[list[str]]:
    """这个块里发出的每一条 SQL，按顺序。

    Listens on the ``Engine`` class rather than one instance: the app's session
    and this fixture's own both end up on sync engines underneath, and pinning
    the count means catching whatever the request actually issued. Copied from
    `test_hot_path_queries.py` — same problem, same handle.
    """
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "after_cursor_execute", _record)


# --- 每行的昵称与脸 ---------------------------------------------------------


def test_each_row_carries_the_nickname_and_the_avatar_that_person_picked(
    client, monkeypatch
):
    """root 里三行：挑过脸的给自己的 id，没挑过的、不在平台上的都给 null。

    三种情况放在一张表里，因为它们是同一条规则的三半：挑过 → 给**他自己那张**的
    id（不是所有人共用的那张默认头像）；没挑过 → null，回退成默认头像 id 会让全
    平台没挑过的人共用一张脸，而认人正是头像唯一的活；平台上没有这个账号 → 也是
    null 而不是报错（部署配置里写错一个名字是允许的，那一行仍然是名单的一份）。
    """
    ids = _seed_profiles(client, {PICKED: "predefined", PLAIN: "default"})
    _roots(monkeypatch, ADMIN, PICKED, PLAIN)

    rows = {row["handle"]: row for row in _list(client, ADMIN)["root"]}
    # root 按 handle 升序 —— 顺带把那个顺序钉住。
    assert [row["handle"] for row in _list(client, ADMIN)["root"]] == sorted(
        [ADMIN, PICKED, PLAIN]
    )

    assert rows[PICKED]["nickname"] == PICKED.upper()
    assert rows[PICKED]["avatar_id"] == ids[PICKED]

    # 没挑过的人身上那一行是 default 型，它的 id 就是「全局默认头像」。这条断言
    # 直接说：那个 id 没有被发出去。
    assert rows[PLAIN]["nickname"] == PLAIN.upper()
    assert rows[PLAIN]["avatar_id"] is None
    assert ids[PLAIN] != ids[PICKED]

    # 没有账号的人：昵称没有就回 null，不回退成 handle（回退之后客户端就分不出
    # 「他叫这个」和「他还没起名字」）。
    assert rows[ADMIN]["nickname"] is None
    assert rows[ADMIN]["avatar_id"] is None


def test_a_root_handle_with_no_account_is_still_a_row_not_an_error(client, monkeypatch):
    """部署配置里写了一个平台上没有的名字：那一行照样在，值给 null，请求不炸。"""
    _roots(monkeypatch, ADMIN, "am-ghost")

    row = next(r for r in _list(client, ADMIN)["root"] if r["handle"] == "am-ghost")
    assert row == {
        "handle": "am-ghost",
        "nickname": None,
        "avatar_id": None,
        # 平台上没有这个账号：三件事全空 —— 没账号、谈不上注册时间、也不是 agent。
        "has_account": False,
        "registered_at": None,
        "is_agent": False,
    }


# --- 每行的账号状态、注册时间与 agent 标记 -------------------------------------


def test_roster_rows_carry_account_state_registration_and_agent_flag(
    client, monkeypatch
):
    """每行多说三件事：平台上有没有这个账号、什么时候注册的、是不是 agent。

    三件事是同一句追问的三个答案：「这行权限是不是死的」。没账号（或已注销）的
    行、是 agent 的行，都是配置里写了但永远用不上的权限 —— 页面要把它们和「没设
    昵称」区分开，靠的是这三个显式字段，而不是猜 `nickname` 是不是 null（没账号、
    没设昵称、agent 的昵称都可以是 null，只有这三格分得开）。
    """
    _roots(monkeypatch, ADMIN, GHOST)
    _seed_profiles(client, {HUMAN: "default", BOT: "default"})
    _add(client, ADMIN, HUMAN)
    _seed_agent_binding(client, BOT)
    _add_roster_row(client, BOT, by=ADMIN)

    data = _list(client, ADMIN)
    root_rows = {row["handle"]: row for row in data["root"]}
    added_rows = {row["handle"]: row for row in data["added"]}

    # 平台上没有这个账号：三件事全空 —— 没账号、谈不上注册时间、也不是 agent。
    assert root_rows[GHOST]["has_account"] is False
    assert root_rows[GHOST]["registered_at"] is None
    assert root_rows[GHOST]["is_agent"] is False

    # 普通行：有账号、注册时间是一个可解析的 ISO 串、不是 agent。
    assert added_rows[HUMAN]["has_account"] is True
    assert datetime.fromisoformat(added_rows[HUMAN]["registered_at"])
    assert added_rows[HUMAN]["is_agent"] is False

    # agent 行：账号是真的（has_account 照答 True），但权限用不上 —— 「这行是死
    # 权限」的那一格是 is_agent，has_account 答不出这句话。
    assert added_rows[BOT]["has_account"] is True
    assert datetime.fromisoformat(added_rows[BOT]["registered_at"])
    assert added_rows[BOT]["is_agent"] is True

    # 增强字段进了 ETag 输入：给 GHOST 补一个**裸账号**（不造 profile，昵称和头像
    # 保持 null —— 对照断言在下面），变的只有 has_account / registered_at 两格，
    # tag 必须翻。不翻的话，一个人「从没有账号变成有账号」会被 304 永远盖住。
    before = client.get("/admin/admins", headers=session_auth_headers(ADMIN)).headers[
        "ETag"
    ]
    _seed_bare_account(client, GHOST)
    r = client.get("/admin/admins", headers=session_auth_headers(ADMIN))
    assert r.headers["ETag"] != before

    ghost = next(row for row in r.json()["data"]["root"] if row["handle"] == GHOST)
    # 对照组：既有字段一个都没变 —— 上面那次翻转只能记在增强字段头上。
    assert ghost["nickname"] is None
    assert ghost["avatar_id"] is None
    assert ghost["is_agent"] is False
    assert ghost["has_account"] is True
    assert datetime.fromisoformat(ghost["registered_at"])


# --- 加 / 删 -----------------------------------------------------------------


def test_adding_and_removing_return_the_whole_updated_roster(client, as_admin):
    """回包是**加完之后那一份**，`created` / `removed` 说得出这次有没有真写进去。

    回整份而不是回一行：加完之后页面上的两块都可能变，让客户端再拉一次等于把
    「刚改完的状态」拆成两个请求，中间那一下页面是旧的。重复加不是失败，但页面要
    说得出区别；删一个本来就不在名单里的人，正确的答案是 200 加 `removed=false`。
    """
    ids = _seed_profiles(client, {PICKED: "predefined"})

    added = _add(client, as_admin, PICKED)
    assert added["created"] is True
    # 整份都在：root 里有操作者，added 里有刚加的人，而且这个人带着自己的昵称和脸。
    assert {r["handle"] for r in added["root"]} == {as_admin}
    rows = {r["handle"]: r for r in added["added"]}
    assert rows[PICKED]["nickname"] == PICKED.upper()
    assert rows[PICKED]["avatar_id"] == ids[PICKED]
    # added 那一半还多两件事：「谁加的」和「什么时候」。
    assert rows[PICKED]["added_by_handle"] == as_admin
    assert rows[PICKED]["created_at"]

    # 再加一次：不是失败，但 `created` 说得出这次没写进去，名单里也只有一行。
    again = _add(client, as_admin, PICKED)
    assert again["created"] is False
    assert [r["handle"] for r in again["added"]] == [PICKED]

    # 删掉：整份回来了，added 空了，`removed` 说得出真删了一行。
    removed = _remove(client, as_admin, PICKED)
    assert removed["removed"] is True
    assert removed["added"] == []
    assert {r["handle"] for r in removed["root"]} == {as_admin}


def test_the_root_admin_cannot_be_removed_but_a_page_one_can(client, as_admin):
    """根管理员删不掉（409，不是静默不动），页面上加的删得掉，不在名单里也不报错。"""
    _seed_profiles(client, {PLAIN: "default"})

    blocked = client.delete(
        f"/admin/admins/{as_admin}", headers=session_auth_headers(as_admin)
    )
    assert blocked.status_code == 409, blocked.text
    # 这次拒绝没有顺手改动名单：他还在 root 里。
    assert as_admin in {r["handle"] for r in _list(client, as_admin)["root"]}

    _add(client, as_admin, PLAIN)
    assert _remove(client, as_admin, PLAIN)["removed"] is True

    # 本来就不在名单里的人：他的目标是「让这个人不在名单里」，而那个结果已经成立。
    assert _remove(client, as_admin, "am-nobody")["removed"] is False


def test_deleting_a_row_that_is_not_there_is_a_no_op_at_the_route(client, as_admin):
    """从路由这一侧再钉一次「删不存在的行不是错误」—— 这里没有 500，也没有 404。"""
    r = client.delete(
        "/admin/admins/am-never-added", headers=session_auth_headers(as_admin)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["removed"] is False


async def test_two_deletes_of_one_row_at_once_leave_one_winner_and_no_error(
    business_db_factory,
):
    """两个连接同时删同一个 handle：一个删掉，另一个平静地拿到 false，谁都不炸。

    跑的是路由会走的那条语句（`AdminRepository.remove_admin`）—— 路由自己没有第二
    条删法，所以这里不抛异常，响应里就不会有 500。被换掉的写法是「先 SELECT 有没有、
    再删」：两个连接都会 SELECT 到那一行，后 flush 的 DELETE 一行也没匹配上，
    SQLAlchemy 把 `StaleDataError` 抛出来；在路由上那就是一个 500 —— 而这件事的正确
    结果是 200（「这个人不在名单里」已经成立）。

    `client.test_factory` 的 engine 是 NullPool，每个 session 一根自己的连接，所以这
    两个删除是**真的并发**，不是同一根连接上的两条串行语句（那两条永远测不出这个竞
    态）。一个先拿到行锁、另一个在数据库里等着，赢的那次拿到 id、输的那次拿到空结果。
    """
    handle = "am-race"
    async with business_db_factory() as session:
        assert await AdminRepository(session).add_admin(handle, added_by=ADMIN)
        await session.commit()

    async def remove() -> bool:
        async with business_db_factory() as session:
            done = await AdminRepository(session).remove_admin(handle)
            await session.commit()
            return done

    results = await asyncio.gather(remove(), remove())
    assert sorted(results) == [False, True], results

    # 行确实没了：第三次删同样只是 false，没有一行还挂在那儿。
    async with business_db_factory() as session:
        assert await AdminRepository(session).remove_admin(handle) is False


# --- 条件请求 ---------------------------------------------------------------


def test_the_roster_answers_a_conditional_request_with_an_empty_304(client, as_admin):
    """ETag 是带双引号的不透明串；命中就回 304、空 body，但两个头照带。"""
    r = client.get("/admin/admins", headers=session_auth_headers(as_admin))
    assert r.status_code == 200, r.text
    etag = r.headers["ETag"]
    digest = etag[1:-1]
    assert etag == f'"{digest}"'
    # 只钉「带引号、引号里非空、里面不再有引号」。ETag 按 RFC 9110 是不透明串，
    # 客户端从不解析它 —— 钉 `len == 64` 或十六进制字符集，钉的是 sha256 这个实现
    # 选择：把它换成 blake2b 或 base64url，304 的语义、缓存头、两条路全部不变，
    # 用例却会红。行为由下面那几条钉（内容变 tag 就变、命中回空 304）。
    assert digest
    assert '"' not in digest

    matched = client.get(
        "/admin/admins",
        headers={**session_auth_headers(as_admin), "If-None-Match": etag},
    )
    assert matched.status_code == 304, matched.text
    assert matched.content == b""
    # RFC 要求 304 也带这两个头：客户端靠它们刷新缓存里那份。
    assert matched.headers["ETag"] == etag
    assert matched.headers["Cache-Control"] == r.headers["Cache-Control"]

    # 对不上的 tag 走常规那条路：200、body 还在。
    stale = client.get(
        "/admin/admins",
        headers={**session_auth_headers(as_admin), "If-None-Match": '"deadbeef"'},
    )
    assert stale.status_code == 200, stale.text
    assert stale.json()["data"]["root"]


def test_the_roster_is_private_so_no_shared_cache_can_hand_it_to_a_stranger(
    client, as_admin
):
    """`private` 是安全要求，不是性能偏好。

    这份名单说的是「这个平台上谁能看所有人的私密反馈和安全问题」。一个被 CDN 或中间
    代理按 URL 缓存下来的 `public` 响应，等于把它交给任何请求同一个地址的人 —— 哪怕
    那个人不是管理员、甚至没有身份。

    所以这一条单开：把 `private` 换成 `public`，别的断言（ETag、304、`no-cache`）全都
    照绿，只有这里会红。
    """
    r = client.get("/admin/admins", headers=session_auth_headers(as_admin))
    assert "private" in r.headers["Cache-Control"]
    assert r.headers["Cache-Control"] == "private, no-cache"


def test_the_etag_changes_when_the_roster_does(client, as_admin):
    """ETag 只依赖 data 的内容 —— 名单变了就变，变回去也变回去。"""
    _seed_profiles(client, {PLAIN: "default"})

    def etag() -> str:
        return client.get(
            "/admin/admins", headers=session_auth_headers(as_admin)
        ).headers["ETag"]

    before = etag()
    _add(client, as_admin, PLAIN)
    assert etag() != before
    # 变回去：内容一样，tag 就得一样 —— 否则每轮公告一下，304 永远不会命中。这一
    # 条同时排掉了「tag 里混进了时间戳或请求计数」那种写法。
    _remove(client, as_admin, PLAIN)
    assert etag() == before


# --- 鉴权 -------------------------------------------------------------------


def test_the_member_routes_reject_everyone_who_is_not_an_admin(client, as_admin):
    """四个端点一个都不放过非管理员；没身份的连被拒的资格都没有。"""
    stranger = session_auth_headers(STRANGER)

    assert client.get("/admin/admins", headers=stranger).status_code == 403
    assert (
        client.get("/admin/users", params={"q": "a"}, headers=stranger).status_code
        == 403
    )
    assert (
        client.post("/admin/admins", json={"handle": "x"}, headers=stranger).status_code
        == 403
    )
    assert client.delete("/admin/admins/x", headers=stranger).status_code == 403

    # Anonymous is not a 403 either way — there is no one to refuse; 401 or 403 both
    # mean "no identity here".
    assert client.get("/admin/admins").status_code in (401, 403)
    assert client.get("/admin/users", params={"q": "a"}).status_code in (401, 403)
    assert client.delete("/admin/admins/x").status_code in (401, 403)

    # 对照组：管理员进得去。挡上上面那几条的必须是名单，不是这个门本身坏了。
    allowed = client.get("/admin/admins", headers=session_auth_headers(as_admin))
    assert allowed.status_code == 200, allowed.text


# --- 批量而不是按行 ---------------------------------------------------------


def test_the_roster_costs_the_same_number_of_queries_however_many_rows(
    client, as_admin
):
    """名单多一个人，不该多一次往返。

    昵称和头像都在 `UserProfile` 上，按行查就是 N+1：二十个人二十次往返。回包这一
    侧看不出区别（每条都答对了），只有往返次数看得出来。

    量两个规模：先两行、再八行，断言两者**相等**而不是某个固定数字 —— 固定数字只
    钉住今天的实现，相等才钉住真正要守住的那件事：查询数不跟着名单长度走。
    """
    _seed_profiles(client, {f"am-roster-{i}": "default" for i in range(2)})
    for i in range(2):
        _add(client, as_admin, f"am-roster-{i}")

    def count() -> int:
        with _counting_sql() as seen:
            r = client.get("/admin/admins", headers=session_auth_headers(as_admin))
        assert r.status_code == 200, r.text
        return len(seen)

    count()  # 预热：别量到「一个进程只做一次」的那几条语句。
    two_rows = count()

    _seed_profiles(client, {f"am-roster-{i}": "default" for i in range(2, 8)})
    for i in range(2, 8):
        _add(client, as_admin, f"am-roster-{i}")

    assert len(_list(client, as_admin)["added"]) == 8
    eight_rows = count()

    assert eight_rows == two_rows, (
        f"名单从 2 行涨到 8 行，查询数 {two_rows} -> {eight_rows}：昵称或头像在按行查。"
    )
