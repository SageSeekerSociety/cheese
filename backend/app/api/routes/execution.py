"""Scoped tool calls to the room's recorded execution generation."""

import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    GatewayTimeoutError,
    NotFoundError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import dispatch_log, execution
from app.domain.agent.device_hub import DeviceNotReady, DeviceUnreachable
from app.domain.device import owner_reads
from app.domain.topic.models import Topic

router = APIRouter(tags=["execution"])
logger = logging.getLogger(__name__)


class ExecutionRequest(BaseModel):
    method: str
    params: dict = {}


@router.post("/topics/{topic_id}/execution/{resource_id}", include_in_schema=False)
async def execute(
    topic_id: uuid.UUID,
    resource_id: uuid.UUID,
    request: Request,
    payload: ExecutionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    trace_id = "execution-" + uuid.uuid4().hex
    logger.debug(
        "execution_timing stage=handler_start trace=%s mono_ns=%d method=%s tool_id=%s",
        trace_id,
        time.monotonic_ns(),
        payload.method,
        payload.params.get("id", ""),
    )
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    if not claims or claims.get("t") != str(topic_id):
        raise AuthenticationRequiredError("A credential for this room is required")
    # The connection owner survives app releases. Loading the full Topic model
    # makes an unrelated column removal break every tool call on the old owner.
    room = (
        await db.execute(
            select(Topic.id, Topic.project_id, Topic.resource_id).where(
                Topic.id == topic_id
            )
        )
    ).one_or_none()
    if room is None:
        raise NotFoundError("Topic not found")
    if claims.get("p") != str(room.project_id):
        raise ForbiddenError("Execution belongs to another project")
    # The hands are the session's, so the lease is read per session — one room
    # can hold several. The credential names a room and a generation and not a
    # session, which is enough because the executor is still pinned per room
    # (`resolve_pinned_device`): every session here leases the same hands. The
    # day that stops being true, the credential has to say which session it
    # belongs to.
    lease = next(
        (
            hands
            for where, hands in await owner_reads.session_places(db, topic_id)
            if where.get("resource_id") == str(resource_id)
            and (hands or {}).get("kind") == "device"
        ),
        None,
    )
    if (
        lease is None
        or claims.get("r") != str(resource_id)
        or (room.resource_id or room.id) != resource_id
    ):
        raise ConflictError("Execution generation is no longer current")
    if payload.method not in {
        "ping",
        "context",
        "context_fs",
        "invoke",
        "mcp",
        "control",
        "cli",
    }:
        raise ForbiddenError("This executor operation is not available to the session")
    target = lease
    # 发出**之前**写下这次派发，和上面那次 commit 一起落库（结论 57，6.5）。带 id
    # 的调用才记：那个 id 是执行器自己给这次副作用起的名字（``remote_execution/
    # runtime.py`` 的 ``invoke`` 按它判重），而不带 id 的调用按它自己的协议问两遍
    # 和问一遍一样 —— 为一次探活留一行悬着的记录，只会让它变成一件要人确认的事。
    #
    # 这条路由由**设备连接属主**服务，而一次 app 发布会刻意把属主留在旧镜像上
    # （见 `domain/device/owner_reads.py`）。这里加载一个 ORM 模型仍然是安全的，
    # 因为这张表是随这次改动一起建的：认识这个模型的镜像和建表的那次迁移是同一次
    # 发布，而 `deploy-docker.sh` 先跑 `alembic upgrade head` 再换容器。旧属主只是
    # 一行都不记 —— 记不下的那些读出来是「没有派发过」，和今天一模一样。
    key = payload.params.get("id")
    dispatch = (
        dispatch_log.record(db, place_id=topic_id, key=str(key), method=payload.method)
        if key
        else None
    )
    await db.commit()
    logger.debug(
        "execution_timing stage=admitted trace=%s mono_ns=%d",
        trace_id,
        time.monotonic_ns(),
    )
    # The scope check above is the only database work this request needs.  Do
    # not acquire a transaction-scoped advisory lock here: the remote executor
    # call can legitimately stay open for minutes, and holding the request's
    # AsyncSession across it consumes one QueuePool slot per active tool.  Once
    # enough tools are in flight, ordinary page reads wait for
    # db_pool_timeout_s and the whole site appears dead.  The device connection
    # owner already counts active RPCs and gates executor release, so the
    # business route must release its database connection before crossing that
    # boundary.
    #
    # 下面这三个出口就是这条记录的全部分类，**默认那一档是「不写」**：结清成
    # ``failed`` 是在说「这件事确定没发生，重派它安全」，说错了的代价是同一次写做两
    # 遍；而留着不结清读出来是 ``unknown``，代价只是有人被问一句。所以只有确实走不
    # 出这台平台的那一类才结清。
    try:
        answer = await execution.call(
            target, payload.method, payload.params, trace_id=trace_id
        )
    except (DeviceUnreachable, DeviceNotReady):
        # 确定没发出去，而且只有这一档：链路不在（``DeviceUnreachable``），或者链路
        # 在而这台机器的连接器还在自更新（``DeviceNotReady``，同样挡在发出之前）。
        # 那台机器没见过这次调用，所以重派它是安全的。
        #
        # 剩下的都**不结清**，因为它们都盖着「帧已经写出去了」：等结果时链路断掉是
        # ``DeviceOffline``（``drop_transport`` 把在飞的 future 全置成它），而
        # ``DeviceCallError`` 里就有执行器那句「Request accepted; outcome is
        # pending or unknown. Do not replay with a new ID」。把这些记成 ``failed``，
        # 就是让扫底原样再发一遍一次可能已经落地的写。
        await _settle(db, dispatch, dispatch_log.Outcome.failed)
        raise
    except TimeoutError as exc:
        # The machine holds its link and does not answer. That is a fault of the
        # far end, and answering 500「服务器内部错误」 blames the one process it
        # cannot be — the same reasoning, and the same status, as the connection
        # owner's own RPC path in `device_connection_app.call`.
        #
        # 这一行**不写回**：超时之后这次调用做没做过，这一侧不知道，而写下一个猜出
        # 来的结果，就是把「不知道」变成一句下一次重派会当真的话。留着不结清，它读
        # 出来就是 ``unknown``，重派路径会把它交给人（结论 57）。
        raise GatewayTimeoutError("机器没有在时限内回应这次执行调用") from exc
    else:
        await _settle(db, dispatch, dispatch_log.Outcome.done)
        return answer
    finally:
        await db.rollback()
        logger.debug(
            "execution_timing stage=handler_end trace=%s mono_ns=%d",
            trace_id,
            time.monotonic_ns(),
        )


async def _settle(
    db: AsyncSession, dispatch: uuid.UUID | None, outcome: dispatch_log.Outcome
) -> None:
    """把结果写回那一行，并在这里就提交。

    在执行调用**之后**才重新向池子要连接，是上面那段注释的另一半：整个远端调用期间
    这个请求手上不能有连接，而这一次结清只占它几毫秒。
    """
    if dispatch is None:
        return
    await dispatch_log.settle(db, dispatch, outcome)
    await db.commit()
