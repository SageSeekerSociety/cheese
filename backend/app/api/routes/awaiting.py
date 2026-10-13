"""待我处理：跨项目的那一份清单。

看板回答「这个项目现在有什么在等人」，这里回答「**我**现在要处理什么」—— 同一份规则
（`room_task/presentation.py`）、同一份收件人判据（`delivery/addressing.py`，投递用的
也是它），范围换成我能看见的全部项目。为什么它不能由通知表拼出来，写在
`room_task/awaiting.py` 的模块说明里。

查询留在 API 层，不进领域：这一份要问项目、房间、活、验收卡、消息块、轮次六个地方，
而项目看板那两份（`projects.list_project_tasks` / `topics.list_topics`）也是在路由里
把批查询拼起来的 —— 同一个形状，同一个理由。查询本身在 `app/api/awaiting_deps.py`：
推送（`routes/notifications_live.py`）读的也是它，而路由模块之间不互相 import（C4
`routes-independent`）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.awaiting_deps import waiting_items
from app.api.deps import get_chat_service
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.domain.agent.chat import ChatService

router = APIRouter(prefix="/awaiting-me", tags=["awaiting"])


@router.get("")
async def list_awaiting_me(
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """我现在要处理的事项，最近动过的在前。

    访客拿到空列表，不是错误：这个清单的定义就是「点到了我的那些」，而没有身份的调
    用者没有被任何一件事点到。
    """
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        return ok(page([], 0))
    items = await waiting_items(db, chat, handle=who.handle, user_id=who.user_id)
    rows = [item.as_dict() for item in items]
    return ok(page(rows, len(rows)))
