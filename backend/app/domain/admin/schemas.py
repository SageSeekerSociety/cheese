"""平台管理员这一域的请求体。

只有「加一个人」这一件事有请求体：名单的读、删、候选搜索都是路径或查询参数，
`AdminService.admins_out` 那份名单本来就是 dict（两个列表，不是一张表）。
"""

from pydantic import BaseModel, Field


class AdminAdd(BaseModel):
    """``POST /admin/admins`` 的请求体 —— 一个人。

    `handle` 而不是 user id，和这一族表里所有「谁」的写法一致（`author_handle`、
    `assignee_handle`）；名单按 handle 精确匹配，所以长度跟着 `String(64)` 走。

    只判「非空、不超长」这些形状上的事：**这个 handle 在平台上存不存在、是不是
    agent** 都要读库，是 `AdminService.add_admin` 的判断 —— 路由拿不到判断权也
    就不会漏掉它。
    """

    handle: str = Field(min_length=1, max_length=64)
