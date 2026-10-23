"""Starting a seat's session: where it is placed, and with what.

``ensure`` answers the one question the rest of the room asks before it can say
anything to a teammate — which session is this seat's, starting one if there is
not one yet. What it starts with is the channel's (``prepare_session``), what it
comes back with is the core's (``host.start``), and what the room keeps is a
``Live`` (``_attach``). A harness whose runner has to be reached again after a
restart gets there by ``lifecycle.recover``, which adopts rather than starts.

``RUNNER_GONE_S`` is read here (``gone_after_s``, the core's own patience with a
runner) and in ``reading._died``; it is assigned in the facade and read through
the package so patching ``app.domain.agent.room.sessions`` reaches this call.
"""

from __future__ import annotations

import json

from app.core import model_roles
from app.core.config import settings
from app.domain.agent import machine_launcher
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import Placement, mint_session_token
from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from app.domain.agent.place import seat_key
from app.domain.agent.room import sessions as pkg
from app.domain.agent.room.sessions.records import Live, _home
from app.domain.agent.room.stopwatch import Stopwatch
from app.domain.agent.session_host.contract import (
    EFFORT_ENV,
    Access,
    ModelSettings,
    Owner,
    SessionError,
    SessionSpec,
)
from app.domain.agent.session_host.contract import SessionRef as CoreRef
from app.domain.project_skill.service import session_skill_files


async def ensure(
    svc: pkg.RoomSessions,
    session: SessionRef,
    *,
    system_prompt: str,
    resume_token: str | None = None,
    model: str | None = None,
    env: dict[str, str] | None = None,
    acting: str | None = None,
    needs_place: bool = True,
    keeps_nothing: bool = False,
    phases: dict[str, float] | None = None,
) -> Live:
    """The seat's session, started where the room's placement puts it if it
    has to be. ``keeps_nothing``: a 支线's session, whose work stays on the
    machine and never reaches the project. ``phases``, when given, receives
    how long each step took (`send` logs it)."""
    mark = Stopwatch(phases)
    seat = svc._seat_of(session)
    if svc._draining(seat):
        # Starting one now would close the old runner's screen in the middle
        # of the work it is being left to finish. A turn waits for the drain
        # at admission (``admitted_initial``); a start that never passes there
        # (the platform's own work, the memory dream) fails, and that work
        # runs again when its own schedule next takes it up.
        # i18n-exempt: only starts that skip admission get here, and they log it
        raise SessionError(
            "This seat's runner is finishing work another release started; "
            "it is replaced once that is done"
        )
    previous = svc.live.get(seat)
    if previous is not None and acting is not None and acting != previous.acting:
        # A different teammate is taking this seat over; the conversation
        # that belonged to the last one does not carry over to them. Other
        # seats in the same room are not this call's business.
        await svc.interrupt(session)
        await svc.close(session)
    mark("ensure_close")
    precheck = await svc.channel.precheck(session, needs_place=needs_place)
    assert isinstance(precheck, Placement)
    mark("ensure_precheck")
    # WHO acts with it. The caller pins a teammate when a message named one;
    # unnamed, it is the agent the machine resolver resolved for this room.
    # The room itself never answers: it may seat several agents, and a name
    # signed into a token cannot be taken back.
    agent = acting or precheck.agent_handle
    placed: dict = {}

    # A task's or a 支线's session is a conversation of its own beside the
    # room's, for the same agent: its state and every file it starts from
    # live apart from the room seat's.
    state_key = seat_key(agent, session.inner_id)

    def place(resource) -> dict:
        placed.update(
            harness=svc.harness,
            agent_handle=agent,
            state=machine_launcher.state_dir(
                session.project_id, resource, svc.harness, state_key
            ),
        )
        return placed

    async with svc.channel.prepare_session(
        session=session,
        token=mint_session_token(session.project_id, session.conversation_id, agent),
        env=env,
        precheck=precheck,
        runtime_factory=place,
        scratch=keeps_nothing,
    ) as prepared:
        ref = CoreRef(svc.harness, _home(placed["state"]))
        launched_env = dict(prepared.env)
        # The teammate's thinking effort, said to the harness as a setting.
        effort = launched_env.pop(EFFORT_ENV, None)
        spec = SessionSpec(
            system_prompt=system_prompt,
            model=model or model_roles.model("agent_model"),
            gone_after_s=pkg.RUNNER_GONE_S,
            resume_token=resume_token,
            model_settings=ModelSettings(effort=effort),
            env={
                **launched_env,
                "CHEESE_PROJECT": str(session.project_id),
                # The conversation: a room, or the task this session works.
                "CHEESE_TOPIC": str(session.conversation_id),
                "CHEESE_AUTHOR": prepared.agent_handle,
                # Part of the launch, so a 支线 becoming a task relaunches
                # an idle session with a credential whose work is kept.
                **(
                    {"CHEESE_KEEPS_NOTHING": "1" if keeps_nothing else "0"}
                    if session.inner_id is not None
                    else {}
                ),
                # 这次部署多给的一张牌：请平台压缩自己这个会话（v1 只在 dev
                # 开着看）。写进环境是唯一一处决定——`sandbox/cheese` 按它挑
                # 清单、也按它放行，三个骨架都不各自判断。
                **({"CHEESE_COMPACT_TOOL": "1"} if settings.agent_compact_tool else {}),
            },
            acting=prepared.agent_handle,
            skills=session_skill_files(session.project_id),
            # The marker platform instructions carry in this room, so the
            # one a harness raises is not a second convention to learn.
            notice=PLATFORM_NOTICE,
        )
        access = Access(
            prepared.token,
            json.loads(prepared.env["CHEESE_EXECUTION_TARGET"]),
            host=prepared.device_id,
            owner=Owner(
                session.project_id,
                session.conversation_id,
                prepared.env["CHEESE_RESOURCE_ID"],
                session.agent_handle,
                prepared.agent_handle,
                prepared.agent_user_id,
                seat=state_key,
            ),
        )
        mark("ensure_prepare")
        status = await svc.host.start(ref, spec, access)
        mark("ensure_start")
    attached = await svc._attach(
        Live(
            session,
            ref,
            prepared.agent_handle,
            status.conversation,
        )
    )
    mark("ensure_attach")
    # Compared with what it would be started with now: up to date, or a
    # relaunch the channel owes (``prewarm_due``). A runner another release
    # started is replaced by now (``ClaudeCodeDriver.launch``).
    svc.unchecked.discard(seat)
    svc.replace_due.pop(seat, None)
    return attached
