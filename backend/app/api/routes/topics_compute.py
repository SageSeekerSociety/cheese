"""The room's work computer, and the lease a session takes on it.

Fifth slice of `app/api/routes/topics.py` (arch review C-backend.md section 3.3),
after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175) and `topics_side_routes.py` (#2190). topics.py is
3,333 lines against a 1,500-line cap that only ratchets down.

What moves: the three routes that answer "which machine does this room work on,
and may this session use it" -- `GET /topics/{topic_id}/compute-profile`,
`PUT /topics/{topic_id}/compute-profile`, and
`POST /topics/{topic_id}/sessions/{session_id}/work-lease` on the machine it
names -- together with the `WorkLeaseRequest` body the lease route takes.

一个话题一个容器（2026-09-28 决定，推翻结论 60）：这一项是整间房的选择（带
`task` 时是那个任务自己的），这三条路由就是它的读、写和「让这一代会话开工」；
topics.py 里没有别处碰它，所以这个组是自己一个整体，搬走不欠任何边界。

What stays behind, and why. `ProjectRepository` is the one name read here that
topics.py merely imports, and it is imported from topics.py rather than from
`app.domain.project.repositories` on purpose -- the shape `topics_preview.py` and
`topics_side_routes.py` use, for the same reason: the guard in
`tests/unit/test_domain_import_guard.py` ratchets (route module, repository
module) pairs, and a direct import would add a line to that ratchet. topics.py
still reads `ProjectRepository` in three handlers of its own, so its line stays
matched. `.importlinter` is untouched: nothing
here imports an `app.domain.*.models` module, so the C2 baseline does not move
either.

Everything else here keeps its home and is imported from where it is defined;
the names topics.py read only for these routes -- `asyncio`, `asdict`,
`Request`, `project_device_online`, `device_hub`, the eight `market` names,
`sql_device_service`, `HostPool`, `gate` and `propose` -- leave its
imports with them. topics.py imports nothing from this module, so there is no
cycle.

Ordering. This module sorts after `topics.py` and after the other `topics_*`
modules that come before it (`_` > `.`, and `compute` < `side`), so its three
paths mount later in the route table than they did inside topics.py. No route
registered before them -- in topics.py or in the modules mounted between -- has a
parameter where `compute-profile`, `sessions` or `work-lease` sits, so none of
the three loses its first match; resolving every path in the table confirms each
still reaches the handler it did before, now under
`app.api.routes.topics_compute`.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import asyncio
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep, require_seated_agent
from app.api.deps import project_device_online
from app.api.response import ok
from app.api.routes.topics import DbSession, ProjectRepository
from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.agent.market import (
    COMPUTE_DEVICE,
    VISIBILITY_HOST,
    cloud_vm_provisionable,
    compute_default_name,
    compute_listings,
    compute_selectable,
    visibility_listings,
)
from app.domain.device.supply import (
    Supply,
    Visibility,
    default_visibility,
    sandbox_unavailable,
)
from app.domain.device.wiring import sql_device_service
from app.domain.machine.services import HostPool
from app.domain.policy import gate
from app.domain.policy.proposals import propose
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


async def _task_of(db, resolver, topic, task_id: uuid.UUID | None):
    """The task whose work computer this request is about, or None for the
    room's. A task session's credential is about its own task even when it
    names none."""
    task_id = task_id or resolver.task_scope()
    if task_id is None:
        return None
    resolver.require_task_scope(task_id)
    return await TaskService(db).require_in_room(topic.id, task_id)


@router.get("/{topic_id}/compute-profile")
async def get_topic_compute_profile(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
) -> dict:
    """The room's work computer — or with `task`, that task's: the one choice
    every session of that conversation works on. A session that has not
    started answers the same choice, the machine it will get."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    the_task = await _task_of(db, resolver, topic, task)
    project = await ProjectRepository(db).get(topic.project_id)
    from app.domain.agent.compute_configs import (
        place_choice,
        project_configs,
        works_tasks_of,
    )

    configs = project_configs(project.settings if project else None)
    choice = place_choice(topic, the_task, project.settings if project else None)
    current = choice.profile
    device_online = await project_device_online(db, topic.project_id)
    device_service = sql_device_service(db)
    devices = await device_service.list_devices_for_project(topic.project_id)
    if the_task is not None:
        # Someone else's own computer is not offered for this task.
        devices = [
            device
            for device in devices
            if await works_tasks_of(
                db, device.device_id, project, the_task.owner_handle
            )
        ]
    binding = await device_service.topic_binding(topic_id) if the_task is None else None
    if current == COMPUTE_DEVICE and binding is not None and choice.device_id is None:
        # 「自动选一台」的房间，第一轮钉下的是哪一台。
        choice.device_id = binding.device_id
        if topic.compute_config is None:
            named = next((d for d in devices if d.device_id == binding.device_id), None)
            choice.name = named.name if named else None
    # #282 §四 / #358 · whether an agent in THIS room can see a whole enrolled
    # machine. 一个话题一个容器（2026-09-28 决定，推翻结论 60）：房间里的会话看的
    # 都是同一台机器，而它就是房间那一项算出来的那台，所以读那一项就够了。Surfaced
    # so the room shows a visible safety badge instead of the platform granting
    # whole-machine access silently (原则八).
    from app.domain.machine.session_reports import session_machines
    from app.domain.machine.session_work import room_machine_visibility

    if the_task is None:
        visibility = await room_machine_visibility(
            db, topic, project.settings if project else None
        )
    elif current == COMPUTE_DEVICE and choice.device_id:
        visibility = await device_service.room_visibility(topic_id, choice.device_id)
    else:
        visibility = None
    sessions = await session_machines(
        db, topic, the_task.id if the_task is not None else None
    )
    effective_visibility = visibility.value if visibility is not None else None
    return ok(
        {
            "current": current,
            "choice": choice.model_dump(),
            "project_default": configs.default.model_dump(),
            # A task with no choice of its own works on its room's.
            "follows_room": the_task is not None and not the_task.compute_config,
            # A machine id only has selection meaning under the self-hosted pool.
            # A cloud session's sandbox sits on a platform host nobody chooses.
            "device_id": (
                binding.device_id
                if current == COMPUTE_DEVICE and binding is not None
                else choice.device_id
            ),
            "devices": [
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    "online": device_hub.is_online(device.device_id),
                    # Whether the reader may give a room this whole machine.
                    "owned": actor.via == "token"
                    and actor.user_id == device.owner_user_id,
                    # Why a session here cannot have a sandbox, in the reader's
                    # language, or None (also before the machine said hello).
                    "sandbox_unavailable": (
                        reason.descriptor()
                        if (
                            reason := sandbox_unavailable(
                                device_hub.target(device.device_id)
                            )
                        )
                        is not None
                        else None
                    ),
                }
                for device in devices
            ],
            # Each agent session and its machine — the room's, for every one of
            # them; `choice` is None for one that has not taken hands yet and
            # will be given `choice` above.
            "sessions": [
                {k: v for k, v in row.items() if k != "visibility"} for row in sessions
            ],
            "profiles": [
                asdict(v)
                for v in compute_listings(settings, device_online=device_online)
            ],
            # Whether cloud also offers a whole VM per session (`whole_machine`).
            "cloud_vm_available": cloud_vm_provisionable(settings),
            "visibility": {
                "options": [asdict(v) for v in visibility_listings()],
                # "host" | "isolated" | null (no agent here on an enrolled machine).
                "effective": effective_visibility,
                # The one boolean the room's badge keys on: this turn can see and
                # operate the whole machine. Its wording is the reader's language
                # (frontend `work.roomMachine.wholeMachineNotice`), not this payload's.
                "machine_access": effective_visibility == VISIBILITY_HOST,
            },
        }
    )


class WorkLeaseRequest(BaseModel):
    env: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=660, gt=0, le=660)


@router.post("/{topic_id}/sessions/{session_id}/work-lease")
async def acquire_session_work_lease(
    topic_id: uuid.UUID,
    session_id: uuid.UUID,
    body: WorkLeaseRequest,
    request: Request,
    db: DbSession,
) -> dict:
    from app.core.errors import AuthenticationRequiredError
    from app.core.sandbox_auth import scoped_token_claims
    from app.domain.machine import session_work as work_lease

    token = request.headers.get("x-cheese-token", "")
    claims = scoped_token_claims(token)
    if claims is None:
        raise AuthenticationRequiredError("A session execution credential is required")
    topic = await TopicService(db).get_or_404(topic_id)
    await require_seated_agent(
        db, token, project_id=topic.project_id, topic_id=topic_id
    )
    try:
        # body.timeout caps how long the caller waits for a machine being
        # prepared (wait_s). It is not the time to answer: a session that will
        # not wait asks with ~0, and bounding the whole call by that answered
        # 504 even when its machine was ready.
        async with asyncio.timeout(max(body.timeout, work_lease.PREPARING_WAIT_S)):
            return ok(
                await work_lease.ensure(
                    db,
                    topic_id=topic_id,
                    session_id=session_id,
                    claims=claims,
                    token=token,
                    env=body.env,
                    wait_s=min(work_lease.PREPARING_WAIT_S, body.timeout),
                    gone=request.is_disconnected,
                )
            )
    except TimeoutError:
        # One step of preparing the machine outlasted this request. That is
        # still a machine being prepared, so it answers as one: the session's
        # client asks again until its own deadline (`executor_transport.acquire`).
        # A 504 here reached the agent as a failed tool call it read as final,
        # and it slept instead of letting the next call wait.
        return ok({"unavailable": str(say("workComputerPreparing")), "preparing": True})


@router.put("/{topic_id}/compute-profile")
async def set_topic_compute_profile(
    topic_id: uuid.UUID,
    body: dict,
    request: Request,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
) -> dict:
    """The room's work computer, or with `task` that task's.

    Every session working on the choice moves with it, each pushing its work
    first (``machine/session_work.request_choice``): for the room, its own
    sessions and those of tasks that follow it; for a task, the task's. Only
    the task's owner changes a task's, or its own session; a task session's
    credential changes its own task's, never the room's. A person's own
    computer works only that person's tasks.
    """
    from pydantic import ValidationError as SchemaError

    from app.domain.agent.compute_configs import (
        ComputeChoice,
        machine_policy_call,
        place_choice,
        standard_choice,
        validate_choice,
        works_tasks_of,
    )
    from app.domain.machine import session_work as work_lease

    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    await TopicService(db).lock_for_execution(topic_id)
    # 一张签出来的会话凭据能改这一间房，但只能改它自己那一代的那一间：房间重开换了
    # 代，旧凭据改不动新房间（它手里那条会话已经不属于它了）。
    from app.core.sandbox_auth import scoped_token_claims

    claims = scoped_token_claims(request.headers.get("x-cheese-token", "")) or {}
    scoped_session = claims.get("session") if actor.via == "cheese" else None
    if scoped_session and (
        claims.get("t") != str(topic_id)
        or claims.get("r") != str(topic.resource_id or topic.id)
    ):
        raise ForbiddenError("Execution credential does not own this room generation")
    the_task = await _task_of(db, resolver, topic, task)
    if the_task is not None:
        if actor.via == "token" and actor.handle != the_task.owner_handle:
            raise ForbiddenError(say("taskComputeOwnerOnly"))
        if body.get("visibility") is not None:
            raise ValidationError(say("visibilityRoomOnly"))
    name = (body.get("profile") or "").strip() or compute_default_name()
    try:
        choice = ComputeChoice.model_validate(
            body.get("choice")
            or {
                **standard_choice(name).model_dump(),
                "profile": name,
                "device_id": body.get("device_id"),
                "whole_machine": body.get("whole_machine") is True,
            }
        )
    except SchemaError as exc:
        raise ValidationError(say("workComputerConfigInvalid")) from exc
    name = choice.profile
    body = {**body, "device_id": choice.device_id}
    raw_device_id = body.get("device_id")
    if raw_device_id is not None and not isinstance(raw_device_id, str):
        raise ValidationError(say("deviceIdMustBeString"))
    device_id = (raw_device_id or "").strip() or None
    if name != COMPUTE_DEVICE and device_id is not None:
        raise ValidationError(say("deviceIdOwnDeviceOnly"))

    device_online = await project_device_online(db, topic.project_id)
    allowed = {v.id for v in compute_selectable(settings, device_online=device_online)}
    # A named self-hosted machine may deliberately be offline: the topic is pinned
    # now and waits for that exact box. The automatic option keeps the old rule and
    # is selectable only when at least one project-scoped device is online.
    if name not in allowed and not (name == COMPUTE_DEVICE and device_id is not None):
        raise ValidationError(say("computeKindUnavailable", name=repr(name)))
    if body.get("choice") or choice.whole_machine:
        await validate_choice(db, topic.project_id, choice)

    device_service = sql_device_service(db)
    named_device = None
    if device_id is not None:
        scoped_devices = await device_service.list_devices_for_project(topic.project_id)
        named_device = next(
            (device for device in scoped_devices if device.device_id == device_id),
            None,
        )
        if named_device is None:
            raise ValidationError(say("deviceNotInProject"))
    project = await ProjectRepository(db).get(topic.project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if (
        the_task is not None
        and device_id is not None
        and not await works_tasks_of(db, device_id, project, the_task.owner_handle)
    ):
        raise ValidationError(say("deviceOthersOwn"))

    # What the room sees of that machine: its own sandbox, or the whole machine.
    # Left out, a room keeps what it has there and a new binding gets the
    # default. The whole machine is the owner's to give, and only as a person:
    # an agent asking with its session credential would be a sandboxed room
    # letting itself out.
    visibility = None
    if body.get("visibility") is not None:
        try:
            visibility = Visibility(body["visibility"])
        except ValueError as exc:
            raise ValidationError(say("visibilityInvalid")) from exc
        if named_device is None or named_device.supply is not Supply.self_hosted:
            raise ValidationError(say("visibilityNeedsDevice"))
        if visibility is Visibility.host and (
            actor.via != "token" or actor.user_id != named_device.owner_user_id
        ):
            raise ForbiddenError(say("visibilityHostOwnerOnly"))

    # 要一台机器，先过项目的档位策略（结论 40 后半）。闸门和模型那一侧是同一个
    # （`domain/policy/gate.py`）：撞上策略的调用不报错、也不挂着等，它变成一条给
    # 人的提议——自托管那台机器的提议收件人就是**机主本人**（结论 40「要那台机器
    # 的主人点头」），Cloud 花的是项目的钱，收件人是项目的主人。
    #
    # 放在这里而不是更早：前面几步在答「这个选择本身成不成立」（池接没接入、设备
    # 属不属于这个项目），闸门答的是「这个成立的选择可不可以自己发生」。
    policy = gate.policy_of(project.settings)
    # 不限档的项目——今天的每一个——连这次调用都不必写出来：构造它要再列一遍项目设
    # 备、再取一次机主，而不限档时判决与那几条查询无关。
    verdict: gate.Allowed | gate.Proposal | None = None
    if not policy.lets_everything_through:
        call = await machine_policy_call(
            db, project=project, topic=topic, choice=choice
        )
        # 「超档怎么办」全仓只有 `policy/gate.py` 回答，路由自己答一遍就是第二份
        # 答案：项目把这一档的处置写成 `deny` 时，这里要的是一次**看得见的**拒绝
        # （`OverTier` 抛出去，不变量 I27），而不是一条等人点头的提议 —— 提议读起
        # 来像「再等等」，拒绝说的是「这条路不通」。
        verdict = gate.check(call, policy, actor.handle)
    if isinstance(verdict, gate.Proposal):
        # 这次调用没有发生：绑定不写，`topic.compute_config` 不动。房间里多的
        # 是一条提议，下一步在 approver 手上。
        await propose(db, verdict, place_id=topic_id)
        await db.flush()
        # 报的是这个房间**现在**的算力，也就是同一秒 GET 会报的那一份 —— 它由
        # `room_choice` 算出来，不是 `topic` 上那一列还没被写过的值。第一轮之前
        # 的房间上它们本来就是空的，直接吐出去等于告诉客户端「这个房间没有算力
        # 选择」，而 GET 同时在说它继承了项目默认。同一个资源两个接口两种说
        # 法，先信谁？
        current = place_choice(topic, the_task, project.settings)
        return ok(
            {
                "current": current.profile,
                "choice": current.model_dump(),
                "device_id": current.device_id,
                "proposal": {
                    "approver": verdict.approver,
                    "tier": verdict.call.tier,
                    "content": verdict.content,
                },
            }
        )

    # 到这里这次调用**真的要发生**，Cloud 花的是项目的钱，所以问一句花钱的这位有
    # 没有这个权。它在闸门之后而不是之前：`require_use_authority` 要的是一个登录
    # 用户（`actor.via == "token"`），而房间里跑着的那一轮拿的每一张凭据都是
    # `via == "cheese"`（`api/auth.py`）。放在闸门之前，`cheese_machine(profile=
    # "cloud")` 一句 401 撞死在这里，连那条「等项目主人点头」的提议都长不出来——
    # 而那条提议正是 Cloud 这一档该有的产物（结论 23）。变成提议的那一次没有花任
    # 何人的钱，该点头的人就是项目主人本人。
    await HostPool(db).admit_choice(topic.project_id, actor, choice)

    # 房间这一项写下去的同时，房间里的每一条会话都跟着搬：这就是「一个话题一个容
    # 器」落地的地方。写和搬都在 `request_choice` 里，且只有每一条都搬成了才写——
    # 一条推不上去就是整个房间留在原地（它抛出去，路由把它变成一次可见的失败）。
    moved = await work_lease.request_choice(
        db,
        topic_id=topic_id,
        actor=actor,
        choice=choice,
        task=the_task,
        # 人的那一次可以在原来那台够不着时决定不推送——成员名册和设备页的批量切换
        # 用的就是这个开关。会话凭据自己来改时它不成立（`_move_session`）。
        abandon_unpushed=body.get("abandon_unpushed") is True,
        # 设备页的批量切换跳过正在跑任务的房间，而不是把它手上的机器抽走。
        if_idle=body.get("if_idle") is True,
    )
    # The room's pin is the choice itself, before the first turn and after it:
    # 一个话题一个容器（2026-09-28，推翻结论 60），换机器是整个房间搬过去，钉子跟
    # 着搬——在每条会话都搬成之后才动，一条推不上去整个房间连钉子一起留在原地。
    # Release then bind keeps bind_topic_device write-once: the bind never
    # overwrites, an explicit change removes the old pin first. Cloud or
    # 「系统挑一台」 leaves no pin; resolve_pinned_device freezes it next turn.
    if the_task is not None:
        # A task's choice pins nothing: the room's pin is the room's machine.
        return ok(
            {
                "current": name,
                "choice": choice.model_dump(),
                "device_id": device_id if name == COMPUTE_DEVICE else None,
                "proposal": None,
                "warnings": moved["warnings"],
            }
        )
    binding = await device_service.topic_binding(topic_id)
    if binding is not None and (
        name != COMPUTE_DEVICE or binding.device_id != device_id
    ):
        await device_service.release_topic_device(
            topic_id, reason="the room moved to another work computer"
        )
        binding = None
    if (
        binding is not None
        and visibility is not None
        and binding.visibility is not visibility
    ):
        # The session's executor follows at its next start: the install sees
        # the sandbox it is asked for differ from the one running.
        await device_service.release_topic_device(
            topic_id, reason=f"the room's machine access became {visibility}"
        )
        binding = None
    if name == COMPUTE_DEVICE and device_id is not None and binding is None:
        await device_service.bind_topic_device(
            topic_id, device_id, visibility=visibility or default_visibility()
        )
    await db.flush()
    return ok(
        {
            "current": name,
            "choice": choice.model_dump(),
            "device_id": device_id if name == COMPUTE_DEVICE else None,
            "proposal": None,
            "warnings": moved["warnings"],
        }
    )
