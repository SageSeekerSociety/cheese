"""成员管理 —— 平台管理员的名单，页面上加/删。

管理后台的第二块，管的是**平台级**的名单，和反馈没关系：这个模块里出现的每一个字
在反馈功能删掉之后仍然成立。所以它读写的服务是 `AdminService`
（`app/domain/admin/`），门是 `admin_common.PlatformAdminDep`，两者都不从反馈那边
借 —— 反馈只是碰巧也问「这个人是不是管理员」。

单独的模块而不是塞进 `admin_feedback.py`：`main.py` 的自动发现是「一个模块一个
router」，同一个文件里的第二个 `APIRouter` 会被静默丢掉，理由写在那边的 docstring
里；这里是同一件事的第二半。

四个端点（名单、搜账号、加、删），全部要管理员，理由**不是**「这个页面不该被别
人看见」，而是**这份名单决定了谁能看见私密反馈和安全问题**：能改名单就等于能给
自己开门。列表本身也一样要管理员 —— 它说的是「谁在这个平台上能看所有人的私密
反馈」，这份名单不是公开信息。

「根管理员删不掉」**不在这层**，是 `AdminService.remove_admin` 的规则：路由拿不到
判断权，也就没有漏掉它的写法 —— 同 `admin_feedback.py` 那句「按钮出不出现由服务端
说了算」，这里是「删不删得掉由服务端说了算」，前端拿到的名单本来就分两份。
"""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.api.conditional import etag_for_json, if_none_match_hits
from app.api.response import ok
from app.api.routes.admin_common import AdminServiceDep, PlatformAdminDep
from app.domain.admin.schemas import AdminAdd

router = APIRouter(prefix="/admin", tags=["admin"])

#: 这份名单是**平台管理员**专有的，所以只能是 `private` —— 一份被 CDN 或中间代理
#: 缓存下来的管理员名单，等于把它发给任何一个请求同一个 URL 的人。这是安全要求，
#: 不是性能偏好：`public` 在这里是错的，不是慢。`no-cache` 要求每次带 `If-None-Match`
#: 回来问一句，命中 ETag 就回 304（省的是 body，名单本身很小）。
ADMIN_LIST_CACHE_CONTROL = "private, no-cache"


@router.get("/admins", response_model=None)
async def list_admins(
    admins: AdminServiceDep,
    handle: PlatformAdminDep,
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
) -> dict | Response:
    """名单，分成根（只读）和页面上加的（可删）两块给。

    见 `AdminService.admins_out`：两个列表而不是一个带标记的列表，因为这两块
    在页面上的**操作权不一样**，而分组规则不该由前端再定一份。

    条件请求：`ETag` 由 `data` 的规范化 JSON 算出（`etag_for_json`，只依赖内容），
    `If-None-Match` 命中就回 304、空 body —— 页面上的刷新按钮按十次，十次都只是
    这一行 header 的往返。**命中 304 也照带那两个 header**（RFC 要求，客户端要靠
    它们更新缓存）。没命中那条路仍然走 `ok()` 的信封：前端 `request()` 靠
    `{code,message,data}` 解包，只有 304 这一条允许空 body。
    """
    data = await admins.admins_out()
    etag = etag_for_json(data)
    cache_headers = {
        "Cache-Control": ADMIN_LIST_CACHE_CONTROL,
        "ETag": f'"{etag}"',
    }
    if if_none_match and if_none_match_hits(if_none_match, etag):
        return Response(status_code=304, headers=cache_headers)
    # 往注入的 `Response` 上写 header，再回常规的信封 —— 这是「200 带头、304 空体」
    # 两种形状共用一份 header 定义的写法。
    response.headers.update(cache_headers)
    return ok(data)


@router.get("/users")
async def search_users(
    admins: AdminServiceDep,
    handle: PlatformAdminDep,
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    """加人那个选择器的候选：按 handle 或昵称搜账号。

    单开一条而不是复用 `GET /users?q=` —— 那条是**用户目录**接口：它先把一页
    profile 取出来、再在 Python 里过滤，所以搜索只在那一页里成立（这个部署上
    1199 个账号，搜「彭文博」十有八九回空）。加人的时候「搜不到」是要么换个说法
    要么这个人没有账号，两条路都通不了，所以这里的搜索在 SQL 里。

    `q` 至少一个字符：空串搜出的是「平台的前 20 个账号」，那不是一个搜索结果，
    选择器上显示它只会让人以为列表就是这个。
    """
    return ok({"items": await admins.candidates(q, limit=limit)})


@router.post("/admins")
async def add_admin(
    payload: AdminAdd,
    admins: AdminServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """加一个人，返回**更新后的整份名单**。

    回整份而不是回一行：加完之后页面上的两块都会变（这个人可能进的是 `added`，
    也可能被拒），让客户端自己再拉一次等于把「刚改完的状态」拆成两个请求，中间
    那一下页面是旧的。`created` 说明白这次是真加了还是他本来就在 —— 重复点一次
    「添加」不算失败（同 `add_support`），但页面要说得出区别。
    """
    created = await admins.add_admin(payload.handle, by=handle)
    return ok({**(await admins.admins_out()), "created": created})


@router.delete("/admins/{target}")
async def remove_admin(
    target: str,
    admins: AdminServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """把一个人移出名单，同样回整份。

    删一个不在名单里的人不是错误（同 `remove_support`）：他的目标是「让这个人不
    在名单里」，而那个结果已经成立了。`removed` 说得出这次有没有真的删掉一行。
    根管理员到这里会拿到 409，不是静默不动。

    路径参数叫 `target` 而不是 `handle`：这个模块里 `handle` 已经是**操作者**
    （`PlatformAdminDep` 解出来的那个人），两个都叫 `handle` 的话，函数体里
    `remove_admin(handle)` 读起来像在删自己。
    """
    removed = await admins.remove_admin(target)
    return ok({**(await admins.admins_out()), "removed": removed})
