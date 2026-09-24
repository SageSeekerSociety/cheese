"""Scoped tool calls to the room's recorded execution generation."""

import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
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
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceNotReady,
    DeviceUnreachable,
)
from app.domain.device import owner_reads
from app.domain.topic.models import Topic

router = APIRouter(tags=["execution"])
logger = logging.getLogger(__name__)

#: 执行器拒绝受理时的原话（``remote_execution/runtime.py`` 的 ``invoke``）：这个
#: id 上已经有一行了，而输入不是这一个。它挡在 ``invoke`` 碰这次调用之前，所以这
#: 次派发确定没有被执行。这里写的是字符串而不是一个 import：这句话是从**那台机
#: 器**上回来的，而那台机器跑的可能是上一版脚本。对不上就读不出这一档，退回「不
#: 结清」—— 也就是这条记录的默认那一档。
_NOT_ACCEPTED = "Request ID already belongs to different input"


class ExecutionRequest(BaseModel):
    method: str
    params: dict = {}
    timeout: float = Field(default=660, gt=0, le=660)


@router.post("/topics/{topic_id}/execution/{resource_id}", include_in_schema=False)
@router.post(
    "/topics/{topic_id}/execution/session-{resource_id}", include_in_schema=False
)
async def execute(
    topic_id: uuid.UUID,
    resource_id: uuid.UUID,
    request: Request,
    payload: ExecutionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    deadline = time.monotonic() + payload.timeout
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
    lease_generation = None
    if "session" in claims:
        try:
            session_id = uuid.UUID(claims["session"])
            lease_generation = uuid.UUID(claims["lease"])
        except (KeyError, TypeError, ValueError):
            raise AuthenticationRequiredError(
                "An execution session is required"
            ) from None
        owned = await owner_reads.session_execution(db, topic_id, session_id)
    else:
        owned = await owner_reads.legacy_execution(db, topic_id)
        session_id = owned.id if owned is not None else None
    lease = owned.work_lease if owned is not None else None
    if (
        owned is None
        or not lease
        or lease.get("kind") != "device"
        or lease.get("status", "ready") != "ready"
        or (
            lease_generation is not None
            and lease.get("generation") != str(lease_generation)
        )
        or not owned.runtime_location
        or owned.runtime_location.get("resource_id") != str(resource_id)
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
    if not await owner_reads.execution_device_authorized(
        db, target["device_id"], room.project_id
    ):
        raise ForbiddenError("Device no longer serves this project")
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
    #
    # 记的是**哪一个工具**，不是哪一种调用方法：带 id 的只有 ``invoke`` 一种（其余六
    # 种的载荷里都没有 ``id``，见 ``remote_execution/client.py`` 的各调用点），记方法
    # 名那一栏于是每次都长成同一个常量，而这一栏是人在通知里唯一读得到的一句。没有
    # ``tool`` 的（将来某个带 id 的新方法）退回方法名，总比空着强。
    key = payload.params.get("id")
    tool = payload.params.get("tool")
    dispatch = (
        dispatch_log.record(
            db,
            place_id=topic_id,
            key=str(key),
            tool=str(tool) if tool else payload.method,
            session_id=session_id,
            lease_generation=lease_generation,
        )
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
    # 下面这几个出口就是这条记录的全部分类，**默认那一档是「不写」**：结清成
    # ``failed`` 是在说「这件事确定没发生，重派它安全」，说错了的代价是同一次写做两
    # 遍；而留着不结清读出来是 ``unknown``，代价只是有人被问一句。所以只有确实能说
    # 出「那次调用没有被执行」的才结清。
    try:
        answer = await execution.call(
            target,
            payload.method,
            payload.params,
            trace_id=trace_id,
            timeout=max(0.001, deadline - time.monotonic()),
        )
    except (DeviceUnreachable, DeviceNotReady):
        # 帧一个字节都没写出去：链路不在（``DeviceUnreachable``），或者链路在而这台
        # 机器的连接器还在自更新（``DeviceNotReady``，同样挡在发出之前）。那台机器
        # 没见过这次调用，所以重派它是安全的。
        #
        # 等结果时链路才断掉的那一档不在这里：它是 ``DeviceOffline`` 本身
        # （``drop_transport`` 把在飞的 future 全置成它），而那时候帧已经出去了。
        await _settle(db, dispatch, dispatch_log.Outcome.failed)
        raise
    except DeviceCallError as exc:
        # 机器回话了，说的是它自己的失败。只有一句话能撑起「确定没发生」：这个 id 上
        # 已经有一行而输入对不上，``invoke`` 在碰这次调用之前就把它挡了回来。
        #
        # 另一句是执行器的「Request accepted; outcome is pending or unknown. Do not
        # replay with a new ID」，它字面上就在说结果未知，不结清正是它要的。工具自己
        # 抛的异常一句都走不到这里：``invoke`` 把它们包成 ``{"error": …}`` 当结果送回
        # 来，那是一次答复，这条路由收到的是 200。
        if _NOT_ACCEPTED in str(exc):
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
        # 出来就是 ``unknown``。这一轮后来要是被打断、要重派，重派路径先看见的就是
        # 它，那件事于是交到人手上（结论 57）；这一轮要是自己跑完了 —— 沙箱把 504 当
        # 一次工具报错交给模型，这一轮照样跑下去 —— 就没有重派，也没有谁需要读它。
        # 它不会顶掉别的轮次的重发：``unsettled()`` 只回答「这几轮里有什么悬着」，见
        # 那里的 ``since``。
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
    """把结果写回那一行，并在这里就提交。**结不上账不许改变这次调用的结局。**

    在执行调用**之后**才重新向池子要连接，是上面那段注释的另一半：整个远端调用期间
    这个请求手上不能有连接，而这一次结清只占它几毫秒。而那一次「再要一次连接」正是
    这里非吞不可的原因：池子被占满的样子，上面那段注释记的就是那次事故，而现在每一
    次工具调用在远端调用结束之后都要再向池子要一次。

    让它抛出去，代价是这个函数的两种调用位置各毁一样东西：``done`` 那一档排在
    ``return answer`` 前面，一次已经 200 回来的结果会变成 500 交给沙箱；两个 except
    分支里它排在 ``raise`` 前面，于是 409 / 502 连同 ``DeviceCallError`` 要带进房间的
    机器原话一起退化成一个未处理的 500。

    吞掉它，代价只是这一行留着不结清 —— 那本来就是这份记录的默认那一档，读出来是
    ``unknown``，最坏是有人被问一句。
    """
    if dispatch is None:
        return
    try:
        await dispatch_log.settle(db, dispatch, outcome)
        await db.commit()
    except Exception:  # noqa: BLE001 — 见上：结不上账比毁掉这次调用的结局便宜
        logger.exception("dispatch %s not settled as %s", dispatch, outcome.value)
