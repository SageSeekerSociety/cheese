"""The orchestrator's agent lifecycle (Act 4): opening an agent on a device.

An agent has the exact lifecycle of a user (一个 agent 就是一个用户): opening one
creates a user whose username is a random *agent id* and whose password is a random
*agent secret*; it is added to the project (and so inherits the project's shared
permissions). No token is injected into the screen: inside the screen the agent's
``cheese api`` authenticates with the device's own durable token (``Authorization:
Bearer``) plus the screen token (``X-Cheese-Screen`` / ``CHEESE_SCREEN``, a 128-bit
``uuid4`` secret), which ``resolve_actor`` maps back to this agent user — so business
calls are authorized against the agent's own real permissions. This replaced an
injected per-agent JWT (``CHEESE_TOKEN``) that expired mid-session on a long-running
agent; the device token + screen token never expire. The device token authenticates
the control channel; the screen token binds a tool call to its originating agent.

A screen *is* an agent (一个 agent 是一个屏幕). Preconditions: the device is online
and assigned to the project (``serves_project``); the human triggering it is checked
at the route (a member of the project).
"""

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.hub import DeviceHub, HubScreen
from app.agent.models import AgentScreenRow
from app.core.errors import ConflictError, NotFoundError, PreconditionFailedError
from app.domain.device.service import DeviceService
from app.domain.project.models import ProjectMemberRole
from app.domain.project.repositories import ProjectMembershipRepository
from app.domain.user.models import User
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.services import UserAuthService

# The program each agent screen runs, launched through a login shell so it is found
# on the user's real PATH even under a stripped-down service environment.
DEFAULT_AGENT_COMMAND = ["bash", "-lc", "exec claude"]


@dataclass
class OpenedAgent:
    agent_user_id: int
    agent_username: str
    agent_secret: str  # returned once at creation; not persisted in plaintext
    sid: str


@dataclass
class ThreadAgentPolicy:
    """One agent member's delivery inputs for a thread message: its per-thread
    attention ``mode`` (ALL | INTERVAL | MENTION), the INTERVAL minutes (if any), and
    its thread ``role`` (used to pick the highest-priority agent for the triage lock)."""

    user_id: int
    mode: str
    interval_minutes: int | None
    role: int


@dataclass
class _DeferredDelivery:
    agent_user_id: int
    block_id: int
    text: str
    speaker: str


@dataclass
class _TriageEntry:
    """A basic per-thread triage lock: the deferred deliveries waiting to be补投
    plus the timer task that will flush them if no agent releases early."""

    deliveries: list[_DeferredDelivery] = field(default_factory=list)
    task: "asyncio.Task[None] | None" = None


class AgentService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hub: DeviceHub,
        device_service: DeviceService,
        cheeselet_source: Callable[[], str] | str,
        *,
        command: list[str] | None = None,
        agent_email_domain: str = "agents.cheese.local",
        default_avatar_id: int = 1,
        agent_api_base: str = "",
    ) -> None:
        self._sf = session_factory
        self._hub = hub
        self._device_service = device_service
        self._cheeselet = cheeselet_source
        self._command = command or DEFAULT_AGENT_COMMAND
        self._email_domain = agent_email_domain
        self._default_avatar_id = default_avatar_id
        # Where the agent's `cheese api` points (the tool door). Injected as
        # CHEESE_API into the screen; empty falls back to the device's own base.
        self._agent_api_base = agent_api_base
        # The thread each agent was most-recently forwarded a message from, so its
        # `cheese api post-note` (which may omit thread_id) replies into the right group.
        self._last_thread_by_agent: dict[int, int] = {}
        # INTERVAL gating: when each (thread, agent) was last delivered a message.
        self._last_delivery: dict[tuple[int, int], datetime] = {}
        # Basic triage lock: one short-lived deferral per thread (see forward + finish_triage).
        self._triage: dict[int, _TriageEntry] = {}
        # How long a non-mentioned lower-priority agent's wake is deferred.
        self._triage_defer_seconds: float = 30.0

    async def open_agent(
        self, *, device_id: str, project_id: int | None = None, nickname: str | None = None
    ) -> OpenedAgent:
        """Create an agent user and open its screen with an injected session token.

        Agents are project-independent (project is a future wrapper): ``project_id`` is
        optional. When given, the agent is joined to that project and the device must
        serve it; when ``None`` the agent simply runs, owned by the device's owner."""
        if not self._hub.is_online(device_id):
            raise PreconditionFailedError("device is not connected")
        if project_id is not None and not await self._device_service.serves_project(
            device_id, project_id
        ):
            raise PreconditionFailedError("device is not assigned to this project")

        username = "agent-" + secrets.token_hex(6)
        secret = secrets.token_urlsafe(18)
        async with self._sf() as session:
            auth = UserAuthService(
                UserRepository(session),
                UserProfileRepository(session),
                UserFollowingRepository(session),
                UserStatisticsRepository(session),
            )
            try:
                user, _profile = await auth.register_with_password(
                    username=username,
                    nickname=nickname or username,
                    email=f"{username}@{self._email_domain}",
                    password=secret,
                    default_avatar_id=self._default_avatar_id,
                )
            except ValueError as exc:  # USERNAME_TAKEN / EMAIL_TAKEN — a random collision
                raise ConflictError("agent id collision, retry") from exc
            if project_id is not None:
                await ProjectMembershipRepository(session).add_member(
                    project_id=project_id, user_id=user.id, role=ProjectMemberRole.MEMBER
                )
            await session.commit()
            agent_user_id = user.id

        source = self._cheeselet() if callable(self._cheeselet) else self._cheeselet
        # No CHEESE_TOKEN is injected: the agent's `cheese api` authenticates with the
        # device's durable token (its config fallback) + the screen token (CHEESE_SCREEN,
        # a 128-bit secret) — resolved server-side to this agent. Nothing to expire.
        env: dict[str, str] = {}
        if self._agent_api_base:
            env["CHEESE_API"] = self._agent_api_base
        screen = await self._hub.open_screen(
            device_id,
            self._command,
            source,
            project_id=project_id,
            agent_user_id=agent_user_id,
            env=env,
        )
        async with self._sf() as session:  # persist so a restart can re-adopt it
            session.add(
                AgentScreenRow(
                    sid=screen.sid,
                    device_id=device_id,
                    token=screen.token,
                    project_id=project_id,
                    agent_user_id=agent_user_id,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
        return OpenedAgent(
            agent_user_id=agent_user_id, agent_username=username, agent_secret=secret, sid=screen.sid
        )

    async def open_agent_in_project(
        self, *, project_id: int, nickname: str | None = None
    ) -> OpenedAgent:
        """Open an agent in a project, auto-picking an online device assigned to it —
        so the website need not choose a device. Errors if none is available."""
        for device_id in self._hub.online_device_ids():
            if await self._device_service.serves_project(device_id, project_id):
                return await self.open_agent(
                    device_id=device_id, project_id=project_id, nickname=nickname
                )
        raise PreconditionFailedError("no online device is assigned to this project")

    async def forward_chat_to_agents(self, *, project_id: int, text: str, speaker: str) -> int:
        """Forward a group-chat message to every live agent in the project (types it
        into their Claude via the cheeselet's ``say``), telling them to reply with
        ``cheese api post-note`` so their reply lands back in the chat. Returns how
        many agents it reached."""
        prompt = (
            f"[群聊] {speaker}：{text}\n"
            "（这是项目群聊。请用命令 cheese api post-note 'text: 你的回复' 把回复发回群里——"
            "群里的人只看得到你用 post-note 发出的话；不需要回复就忽略。）"
        )
        reached = 0
        for s in self._hub.screens_in_project(project_id):
            await self._hub.call_screen(s.device_id, s.sid, "say", [prompt])
            reached += 1
        return reached

    async def compact(self, *, device_id: str, sid: str) -> None:
        """Ask the agent's Claude to run /compact (the cheeselet's compact fn)."""
        await self._hub.call_screen(device_id, sid, "compact", [])

    async def say(self, *, device_id: str, sid: str, text: str) -> None:
        """Forward a chat message into the agent's Claude screen (the cheeselet's
        ``say`` function types it in). The agent's reply appears live on the 现场."""
        prompt = (
            f"{text}\n"
            "（这是网页聊天，用户只看得到你在终端里的回应；开始/进展/完成时都简短说一句）"
        )
        await self._hub.call_screen(device_id, sid, "say", [prompt])

    def _thread_prompt(self, *, thread_id: int, speaker: str, text: str, mentioned: bool) -> str:
        mention_hint = "【有人 @你】请务必查看并回应本条消息。\n" if mentioned else ""
        return (
            f"[thread:{thread_id}] {speaker}：{text}\n"
            f"{mention_hint}"
            f"（这是群聊消息。请用命令 cheese api post-note 'text: 你的回复' 把回复发回本群"
            f"（如需指定群可加 'thread_id: {thread_id}'）——群里的人只看得到你用 post-note "
            "发出的话；不需要回复就忽略。）"
        )

    async def _say_to_agent(
        self,
        *,
        thread_id: int,
        block_id: int,
        agent_user_id: int,
        text: str,
        speaker: str,
        mentioned: bool,
    ) -> bool:
        """Type a thread message into one online agent's screen. Records the thread as
        its last-forwarded thread (post-note default), the delivery time (INTERVAL
        gating), and the agent's read watermark (已阅 = actually fed to the cheeselet).
        Returns whether the agent had an online screen to receive it."""
        screen = next(
            (s for s in self._hub.all_online_screens() if s.agent_user_id == agent_user_id),
            None,
        )
        if screen is None:
            return False
        prompt = self._thread_prompt(
            thread_id=thread_id, speaker=speaker, text=text, mentioned=mentioned
        )
        await self._hub.call_screen(screen.device_id, screen.sid, "say", [prompt])
        self._last_thread_by_agent[agent_user_id] = thread_id
        self._last_delivery[(thread_id, agent_user_id)] = datetime.now(UTC)
        await self._record_agent_read(thread_id, agent_user_id, block_id)
        return True

    async def _record_agent_read(
        self, thread_id: int, agent_user_id: int, block_id: int
    ) -> None:
        """Advance an agent's read high-water mark now that the message was fed into its
        cheeselet — the agent-side definition of 已阅. Best-effort; never blocks delivery."""
        from app.domain.thread.repositories import ThreadMembershipRepository

        try:
            async with self._sf() as session:
                await ThreadMembershipRepository(session).set_read_watermark(
                    thread_id, agent_user_id, block_id
                )
                await session.commit()
        except Exception:  # read-receipt bookkeeping must never break delivery
            logging.getLogger(__name__).warning(
                "failed to record agent read watermark (thread=%s agent=%s block=%s)",
                thread_id,
                agent_user_id,
                block_id,
                exc_info=True,
            )

    async def forward_message_to_thread_agents(
        self,
        *,
        thread_id: int,
        block_id: int,
        text: str,
        speaker: str,
        agent_policies: list["ThreadAgentPolicy"],
        mentioned_ids: set[int],
    ) -> int:
        """Deliver a thread message to its agent members, gated by each agent's
        attention policy and @-mentions, with a basic triage lock.

        Per-agent eligibility:
          * @mentioned  → always delivered (ignores policy);
          * ALL         → always delivered;
          * INTERVAL(n) → delivered iff ≥ n minutes since its last delivery here;
          * MENTION     → only when @mentioned.

        Triage (BASIC — no reminder / timeout-escalation): a mentioned agent is woken
        immediately; among the *non-mentioned* eligible agents only the highest-role one
        is woken now, the rest are deferred ~30s (``finish_triage`` releases them early).
        Returns how many agents were (or will be) forwarded to."""
        now = datetime.now(UTC)
        # Basic version: a new message flushes any still-pending deferral for the thread.
        await self._flush_triage(thread_id)

        immediate: list[tuple[int, bool]] = []  # (agent_user_id, mentioned)
        broadcast_eligible: list[ThreadAgentPolicy] = []  # non-mentioned & eligible
        for p in agent_policies:
            if p.user_id in mentioned_ids:
                immediate.append((p.user_id, True))
                continue
            if p.mode == "ALL":
                eligible = True
            elif p.mode == "INTERVAL":
                last = self._last_delivery.get((thread_id, p.user_id))
                eligible = last is None or (now - last) >= timedelta(
                    minutes=p.interval_minutes or 0
                )
            else:  # MENTION
                eligible = False
            if eligible:
                broadcast_eligible.append(p)

        deferred: list[ThreadAgentPolicy] = []
        if broadcast_eligible:
            broadcast_eligible.sort(key=lambda p: p.role, reverse=True)
            immediate.append((broadcast_eligible[0].user_id, False))
            deferred = broadcast_eligible[1:]

        reached = 0
        for agent_user_id, mentioned in immediate:
            if await self._say_to_agent(
                thread_id=thread_id,
                block_id=block_id,
                agent_user_id=agent_user_id,
                text=text,
                speaker=speaker,
                mentioned=mentioned,
            ):
                reached += 1

        if deferred:
            entry = _TriageEntry(
                deliveries=[
                    _DeferredDelivery(
                        agent_user_id=p.user_id, block_id=block_id, text=text, speaker=speaker
                    )
                    for p in deferred
                ]
            )
            entry.task = asyncio.create_task(self._triage_timeout(thread_id))
            self._triage[thread_id] = entry
            reached += len(deferred)  # counted as forwarded —補投 fires shortly
        return reached

    async def _triage_timeout(self, thread_id: int) -> None:
        """The fallback wake: if no responsible agent releases the lock in time, flush
        the deferred deliveries anyway."""
        try:
            await asyncio.sleep(self._triage_defer_seconds)
        except asyncio.CancelledError:
            return
        await self._flush_triage(thread_id)

    async def _flush_triage(self, thread_id: int) -> int:
        """Deliver (補投) any deferred wakes for a thread now and clear its lock."""
        entry = self._triage.pop(thread_id, None)
        if entry is None:
            return 0
        if entry.task is not None:
            entry.task.cancel()
        reached = 0
        for d in entry.deliveries:
            if await self._say_to_agent(
                thread_id=thread_id,
                block_id=d.block_id,
                agent_user_id=d.agent_user_id,
                text=d.text,
                speaker=d.speaker,
                mentioned=False,
            ):
                reached += 1
        return reached

    async def finish_triage(self, thread_id: int) -> int:
        """Agent tool door: the responsible agent finished — release the deferred wakes
        for its thread immediately (rather than waiting out the timer)."""
        return await self._flush_triage(thread_id)

    def last_thread_for_agent(self, agent_user_id: int) -> int | None:
        """The thread an agent was most-recently @'d in — post-note's default target."""
        return self._last_thread_by_agent.get(agent_user_id)

    def agent_user_ids_on_device(self, device_id: str) -> list[int]:
        """The user ids of live agents running on a device."""
        return [
            s.agent_user_id
            for s in self._hub.all_online_screens()
            if s.device_id == device_id
        ]

    def list_agents_on_device(self, device_id: str) -> list[dict[str, object]]:
        """Live agents (screens) running on a device, for the 我的Agent page."""
        return [
            {
                "agent_user_id": s.agent_user_id,
                "sid": s.sid,
                "device_id": s.device_id,
                "status": s.vars.get("status"),
                "elapsed": s.vars.get("elapsed"),
                "tokens": s.vars.get("tokens"),
            }
            for s in self._hub.all_online_screens()
            if s.device_id == device_id
        ]

    def list_agents_in_project(self, project_id: int) -> list[dict[str, object]]:
        """Live agents (screens) running in a project, for the workspace to render
        agent avatars (with busy ring + elapsed/tokens from the driver's variables)
        and open their 现场."""
        return [
            {
                "agent_user_id": s.agent_user_id,
                "sid": s.sid,
                "device_id": s.device_id,
                "status": s.vars.get("status"),  # starting|busy|waiting|idle|dead
                "elapsed": s.vars.get("elapsed"),  # e.g. "6m45s"
                "tokens": s.vars.get("tokens"),  # e.g. "19.2k"
            }
            for s in self._hub.screens_in_project(project_id)
        ]

    def screen_project(self, device_id: str, sid: str) -> int | None:
        """The project a screen runs in (for close authorization), or None if the
        screen is unknown / on another device / project-less. Prefer ``screen()`` when
        you must tell 'no screen' apart from 'screen has no project'."""
        screen = self.screen(device_id, sid)
        return screen.project_id if screen is not None else None

    def screen(self, device_id: str, sid: str) -> HubScreen | None:
        """The live screen with this sid on this device, or None if unknown / elsewhere."""
        screen = self._hub.screen(sid)
        if screen is None or screen.device_id != device_id:
            return None
        return screen

    async def close_agent(self, *, device_id: str, sid: str) -> int:
        """Destroy an agent: end its screen and recycle its user (soft-delete). An
        agent's lifecycle is its user's lifecycle. Returns the recycled
        ``agent_user_id``. Idempotent-ish: an unknown screen is a 404."""
        screen = self._hub.screen(sid)
        if screen is None or screen.device_id != device_id:
            raise NotFoundError("Unknown agent screen")
        agent_user_id = screen.agent_user_id
        await self._hub.close_screen(device_id, sid)
        async with self._sf() as session:
            await session.execute(delete(AgentScreenRow).where(AgentScreenRow.sid == sid))
            user = await session.get(User, agent_user_id)
            if user is not None:
                user.deleted_at = datetime.now(UTC)
            await session.commit()
        return agent_user_id

    async def readopt_device_screens(self, device_id: str) -> int:
        """After a server restart, re-register the screens the (still-running) cli
        has on this device — the tmux sessions survived the connection drop."""
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentScreenRow).where(AgentScreenRow.device_id == device_id)
                )
            ).scalars()
            adopted = 0
            for row in rows:
                if self._hub.screen(row.sid) is None:
                    self._hub.adopt_screen(
                        sid=row.sid,
                        device_id=row.device_id,
                        token=row.token,
                        project_id=row.project_id,
                        agent_user_id=row.agent_user_id,
                    )
                    adopted += 1
        return adopted
