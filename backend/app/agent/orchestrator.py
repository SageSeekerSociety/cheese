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
import shlex
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.clone import clone_session
from app.agent.hub import DeviceHub, HubScreen
from app.agent.identity import build_member_dicts
from app.agent.models import AgentScreenRow
from app.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PreconditionFailedError,
)
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

    # Marks a directory trusted in ~/.claude.json so Claude's "Do you trust this folder?"
    # gate never appears (run on the device via exec, in the screen's cwd). Mirrors
    # misc/web-claude PRETRUST_PY; the cheeselet's auto-Enter stays as a fallback.
    _PRETRUST_PY = (
        "import json,os\n"
        "p=os.path.expanduser('~/.claude.json')\n"
        "try:\n d=json.load(open(p))\n"
        "except Exception:\n d={}\n"
        "proj=d.setdefault('projects',{})\n"
        "proj.setdefault(os.getcwd(),{})['hasTrustDialogAccepted']=True\n"
        "json.dump(d,open(p,'w'))\n"
        "print(os.getcwd())\n"
    )

    async def _pretrust(self, device_id: str, cwd: str | None) -> None:
        """Pre-trust the screen's cwd on the device so Claude doesn't open its
        "Do you trust this folder?" gate at startup (deterministic — no keystroke
        racing). Best-effort: a failure just means the gate may appear and the
        cheeselet's auto-Enter handles it."""
        try:
            await self._hub.exec(
                device_id, ["python3", "-c", self._PRETRUST_PY], cwd=cwd, timeout=15
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "pre-trust cwd failed (device=%s cwd=%s)", device_id, cwd, exc_info=True
            )

    def _claude_command(self, *, session_id: str, cwd: str | None, resume: bool) -> list[str]:
        """Build the launch argv for a claudecode screen. We ALWAYS control Claude's
        session id — ``--session-id <uuid>`` for a fresh agent, ``--resume <uuid>`` to
        continue an existing conversation — so the id is a source of truth we minted, not
        something reverse-derived from the device. ``cwd`` is prepended because Claude
        resolves a session's transcript by the cwd's slug, so a resume must run there."""
        flag = "--resume" if resume else "--session-id"
        inner = f"exec claude {flag} {session_id}"
        if cwd:
            inner = f"cd {shlex.quote(cwd)} && {inner}"
        return ["bash", "-lc", inner]

    async def _open_screen_for_user(
        self,
        *,
        device_id: str,
        agent_user_id: int,
        project_id: int | None,
        session_id: str,
        cwd: str | None,
        resume: bool,
    ) -> HubScreen:
        """Open (and persist) a claudecode screen for an EXISTING agent user — the shared
        core of ``open_agent`` (fresh user) and ``recreate_agent`` (reused user). Records
        the Claude session id + cwd so a later recreate can ``--resume`` it."""
        source = self._cheeselet() if callable(self._cheeselet) else self._cheeselet
        # No CHEESE_TOKEN is injected: the agent's `cheese api` authenticates with the
        # device's durable token (its config fallback) + the screen token (CHEESE_SCREEN,
        # a 128-bit secret) — resolved server-side to this agent. Nothing to expire.
        env: dict[str, str] = {}
        if self._agent_api_base:
            env["CHEESE_API"] = self._agent_api_base
        # Pre-trust the cwd so Claude's trust gate never blocks startup.
        await self._pretrust(device_id, cwd)
        command = self._claude_command(session_id=session_id, cwd=cwd, resume=resume)
        screen = await self._hub.open_screen(
            device_id,
            command,
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
                    claude_session_id=session_id,
                    cwd=cwd,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
        return screen

    async def open_agent(
        self,
        *,
        device_id: str,
        project_id: int | None = None,
        nickname: str | None = None,
        cwd: str | None = None,
    ) -> OpenedAgent:
        """Create an agent user and open its claudecode screen (with a freshly minted
        Claude session id).

        Agents are project-independent (project is a future wrapper): ``project_id`` is
        optional. When given, the agent is joined to that project and the device must
        serve it; when ``None`` the agent simply runs, owned by the device's owner.
        ``cwd`` is the working directory to launch Claude from (for a project checkout)."""
        if not self._hub.is_online(device_id):
            raise PreconditionFailedError("device is not connected")
        if project_id is not None and not await self._device_service.serves_project(
            device_id, project_id
        ):
            raise PreconditionFailedError("device is not assigned to this project")

        agent_user_id, username, secret = await self._create_agent_user(
            nickname=nickname, project_id=project_id
        )
        session_id = str(uuid.uuid4())  # we mint Claude's session id (source of truth)
        screen = await self._open_screen_for_user(
            device_id=device_id,
            agent_user_id=agent_user_id,
            project_id=project_id,
            session_id=session_id,
            cwd=cwd,
            resume=False,
        )
        return OpenedAgent(
            agent_user_id=agent_user_id, agent_username=username, agent_secret=secret, sid=screen.sid
        )

    async def _create_agent_user(
        self, *, nickname: str | None, project_id: int | None
    ) -> tuple[int, str, str]:
        """Create a fresh agent user (一个 agent 就是一个用户), optionally joining it to a
        project. Returns ``(agent_user_id, username, secret)``. Shared by ``open_agent``
        and ``clone_agent``."""
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
            return user.id, username, secret

    async def clone_agent(
        self,
        *,
        source_agent_user_id: int,
        target_device_id: str,
        project_id: int | None = None,
        nickname: str | None = None,
        target_cwd: str | None = None,
    ) -> OpenedAgent:
        """Create a NEW agent on ``target_device_id`` whose Claude conversation is FORKED
        from an existing agent's — the「复制自」template. Copies the source agent's
        transcript across machines (via the device exec RPC), rewrites its session id, and
        launches ``claude --resume`` on the target. The source agent must have a recorded
        Claude session and its device must be online. ``target_cwd`` defaults to the
        source's cwd (the same checkout path on the target machine)."""
        if not self._hub.is_online(target_device_id):
            raise PreconditionFailedError("target device is not connected")
        if project_id is not None and not await self._device_service.serves_project(
            target_device_id, project_id
        ):
            raise PreconditionFailedError("target device is not assigned to this project")

        async with self._sf() as session:
            src = (
                (
                    await session.execute(
                        select(AgentScreenRow)
                        .where(AgentScreenRow.agent_user_id == source_agent_user_id)
                        .order_by(AgentScreenRow.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
        if src is None or not src.claude_session_id:
            raise PreconditionFailedError("source agent has no recorded Claude session to copy")
        if not src.cwd:
            raise PreconditionFailedError("source agent has no recorded working directory")
        if not self._hub.is_online(src.device_id):
            raise PreconditionFailedError(
                "source agent's device is offline; bring it online to copy from"
            )

        effective_target_cwd = target_cwd or src.cwd  # default: same checkout path on target
        new_session_id = str(uuid.uuid4())
        await clone_session(
            self._hub,
            source_device_id=src.device_id,
            source_cwd=src.cwd,
            source_session_id=src.claude_session_id,
            target_device_id=target_device_id,
            target_cwd=effective_target_cwd,
            new_session_id=new_session_id,
        )
        agent_user_id, username, secret = await self._create_agent_user(
            nickname=nickname, project_id=project_id
        )
        screen = await self._open_screen_for_user(
            device_id=target_device_id,
            agent_user_id=agent_user_id,
            project_id=project_id,
            session_id=new_session_id,
            cwd=effective_target_cwd,
            resume=True,
        )
        return OpenedAgent(
            agent_user_id=agent_user_id, agent_username=username, agent_secret=secret, sid=screen.sid
        )

    async def attach_agent(
        self,
        *,
        device_id: str,
        project_id: int | None = None,
        nickname: str | None = None,
        session_id: str,
        cwd: str,
    ) -> OpenedAgent:
        """Create an agent whose Claude conversation is an EXISTING session already on
        disk on ``device_id`` — e.g. the user ran ``claude`` there themselves before
        cheese knew about it — instead of minting a fresh one. Just ``claude --resume
        <session_id>`` from ``cwd``; unlike ``clone_agent`` there is nothing to copy, the
        transcript already lives where it's being resumed.

        Guards against attaching a session id some other live screen already has open
        (two ``claude --resume`` processes racing the same transcript file)."""
        if not self._hub.is_online(device_id):
            raise PreconditionFailedError("device is not connected")
        if project_id is not None and not await self._device_service.serves_project(
            device_id, project_id
        ):
            raise PreconditionFailedError("device is not assigned to this project")

        async with self._sf() as db_session:
            existing = (
                await db_session.execute(
                    select(AgentScreenRow).where(AgentScreenRow.claude_session_id == session_id)
                )
            ).scalars().all()
        if any(self._hub.screen(row.sid) is not None for row in existing):
            raise ConflictError("this Claude session is already attached to a live agent")

        agent_user_id, username, secret = await self._create_agent_user(
            nickname=nickname, project_id=project_id
        )
        screen = await self._open_screen_for_user(
            device_id=device_id,
            agent_user_id=agent_user_id,
            project_id=project_id,
            session_id=session_id,
            cwd=cwd,
            resume=True,
        )
        return OpenedAgent(
            agent_user_id=agent_user_id, agent_username=username, agent_secret=secret, sid=screen.sid
        )

    async def recreate_agent(
        self,
        *,
        device_id: str,
        agent_user_id: int,
        resume: bool = False,
        force: bool = False,
        project_id: int | None = None,
        cwd: str | None = None,
    ) -> OpenedAgent:
        """Recreate an agent REUSING its existing user — identity, memberships and all
        authored history are preserved (unlike open_agent, which mints a new user).

        - manual (``resume=False``): open a brand-new Claude session for the same user.
        - restore (``resume=True``): relaunch ``claude --resume <recorded session id>``
          from the recorded cwd, continuing the exact conversation — e.g. after the
          client machine rebooted and the live Claude process died.

        A still-live screen for this agent is only replaced when ``force=True`` (the
        caller is expected to warn the human first); otherwise ``PreconditionFailed``."""
        if not self._hub.is_online(device_id):
            raise PreconditionFailedError("device is not connected")
        if project_id is not None and not await self._device_service.serves_project(
            device_id, project_id
        ):
            raise PreconditionFailedError("device is not assigned to this project")

        # The agent's most recent recorded screen (survives a device disconnect) carries
        # the Claude session id + cwd to resume, and the project default.
        async with self._sf() as session:
            prior = (
                (
                    await session.execute(
                        select(AgentScreenRow)
                        .where(AgentScreenRow.agent_user_id == agent_user_id)
                        .order_by(AgentScreenRow.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            user = await session.get(User, agent_user_id)
        if user is None or user.deleted_at is not None:
            raise NotFoundError("agent user not found")

        # A still-live screen must be explicitly replaced (the caller warns the human).
        live = next(
            (s for s in self._hub.all_online_screens() if s.agent_user_id == agent_user_id), None
        )
        if live is not None and not force:
            raise PreconditionFailedError("agent still active; confirm to replace it")
        if live is not None:
            await self._close_screen_keep_user(live.device_id, live.sid)
        # Drop any stale recorded rows so we don't leave orphan re-adopts pointing at a
        # dead tmux; the fresh screen re-records below.
        await self._forget_agent_screens(agent_user_id)

        if resume:
            if prior is None or not prior.claude_session_id:
                raise PreconditionFailedError(
                    "no recorded Claude session to resume; recreate without resume"
                )
            session_id = prior.claude_session_id
        else:
            session_id = str(uuid.uuid4())
        effective_cwd = cwd if cwd is not None else (prior.cwd if prior is not None else None)
        effective_project = (
            project_id if project_id is not None else (prior.project_id if prior is not None else None)
        )

        screen = await self._open_screen_for_user(
            device_id=device_id,
            agent_user_id=agent_user_id,
            project_id=effective_project,
            session_id=session_id,
            cwd=effective_cwd,
            resume=resume,
        )
        return OpenedAgent(
            agent_user_id=agent_user_id,
            agent_username=user.username,
            agent_secret="",  # user unchanged — no new secret is minted
            sid=screen.sid,
        )

    async def recreate_agent_in_project(
        self,
        *,
        project_id: int,
        agent_user_id: int,
        resume: bool = False,
        force: bool = False,
        cwd: str | None = None,
    ) -> OpenedAgent:
        """Recreate an agent, auto-picking an online device assigned to the project — so
        the website need not choose a device (used e.g. to bring an offline agent back
        after its client machine rebooted). Prefers the device the agent last ran on."""
        # Prefer the agent's last device if it is online and still serves the project.
        async with self._sf() as session:
            prior = (
                (
                    await session.execute(
                        select(AgentScreenRow)
                        .where(AgentScreenRow.agent_user_id == agent_user_id)
                        .order_by(AgentScreenRow.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
        candidates: list[str] = []
        if prior is not None:
            candidates.append(prior.device_id)
        candidates.extend(d for d in self._hub.online_device_ids() if d not in candidates)
        for device_id in candidates:
            if self._hub.is_online(device_id) and await self._device_service.serves_project(
                device_id, project_id
            ):
                return await self.recreate_agent(
                    device_id=device_id,
                    agent_user_id=agent_user_id,
                    resume=resume,
                    force=force,
                    project_id=project_id,
                    cwd=cwd,
                )
        raise PreconditionFailedError("no online device is assigned to this project")

    async def _close_screen_keep_user(self, device_id: str, sid: str) -> None:
        """End a screen on the device and forget its row, WITHOUT recycling the agent
        user (unlike ``close_agent``) — used by recreate, which reuses the same user."""
        await self._hub.close_screen(device_id, sid)
        async with self._sf() as session:
            await session.execute(delete(AgentScreenRow).where(AgentScreenRow.sid == sid))
            await session.commit()

    async def _forget_agent_screens(self, agent_user_id: int) -> None:
        """Delete all recorded screen rows for an agent user (recreate clears stale rows
        before opening the fresh one)."""
        async with self._sf() as session:
            await session.execute(
                delete(AgentScreenRow).where(AgentScreenRow.agent_user_id == agent_user_id)
            )
            await session.commit()

    async def open_agent_in_project(
        self,
        *,
        project_id: int,
        nickname: str | None = None,
        copy_from_agent_user_id: int | None = None,
        target_cwd: str | None = None,
        attach_session_id: str | None = None,
        attach_cwd: str | None = None,
    ) -> OpenedAgent:
        """Open an agent in a project, auto-picking an online device assigned to it —
        so the website (or a fellow project agent) need not choose a device. Errors if
        none is available.

        Same 「模板」 options project agents' owners get via ``/connector/my/agents``:
        ``copy_from_agent_user_id`` forks an existing agent's Claude conversation onto
        the new one (the source must be a member of this same project — checked by the
        caller), and ``attach_session_id``/``attach_cwd`` resumes a Claude session that
        already exists on disk instead of minting a fresh one. At most one of the two
        may be set; neither set falls back to a plain fresh ``open_agent``."""
        for device_id in self._hub.online_device_ids():
            if not await self._device_service.serves_project(device_id, project_id):
                continue
            if copy_from_agent_user_id is not None:
                return await self.clone_agent(
                    source_agent_user_id=copy_from_agent_user_id,
                    target_device_id=device_id,
                    project_id=project_id,
                    nickname=nickname,
                    target_cwd=target_cwd,
                )
            if attach_session_id is not None:
                if not attach_cwd:
                    raise BadRequestError("attach_cwd is required when attaching an existing session")
                return await self.attach_agent(
                    device_id=device_id,
                    project_id=project_id,
                    nickname=nickname,
                    session_id=attach_session_id,
                    cwd=attach_cwd,
                )
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

    def _thread_prompt(
        self,
        *,
        thread_id: int,
        messages: list[tuple[str, str]],
        mentioned: bool,
        truncated: bool = False,
    ) -> str:
        """Render one or more thread messages into a single prompt. ``messages`` is the
        agent's unread backlog in chronological order (speaker, text); the triggering
        message is the last one. When more than one message is unread we deliver the
        whole backlog at once so the agent has the full context it missed, not just the
        latest line."""
        mention_hint = "【有人 @你】请务必查看并回应本条消息。\n" if mentioned else ""
        head = ""
        if truncated:
            head += "（未读消息较多，仅显示最近的一部分）\n"
        elif len(messages) > 1:
            head += "（以下是你未读的群聊消息，最后一条是刚到的）\n"
        body = "".join(f"[thread:{thread_id}] {speaker}：{text}\n" for speaker, text in messages)
        return (
            f"{head}{body}"
            f"{mention_hint}"
            f"（这是群聊消息。请用命令 cheese api post-note 'text: 你的回复' 把回复发回本群"
            f"（如需指定群可加 'thread_id: {thread_id}'）——群里的人只看得到你用 post-note "
            "发出的话；不需要回复就忽略。）"
        )

    # Cap on how many unread messages we feed at once, so a long-neglected thread can't
    # blow up the prompt; older-than-cap unread messages are dropped with a note.
    _UNREAD_CAP = 50

    async def _collect_unread(
        self,
        *,
        thread_id: int,
        agent_user_id: int,
        block_id: int,
        fallback_speaker: str,
        fallback_text: str,
    ) -> tuple[list[tuple[str, str]], bool]:
        """Gather the agent's unread backlog for a thread — every message after its read
        watermark up to and including the triggering block — as (speaker, text) pairs in
        chronological order, plus whether the list was truncated to ``_UNREAD_CAP``. The
        agent's own messages and deleted tombstones are skipped. Best-effort: any failure
        (or an empty backlog, e.g. a re-delivery whose watermark already passed the block)
        falls back to the single triggering message so delivery never breaks."""
        from app.domain.block.repositories import BlockRepository
        from app.domain.thread.repositories import ThreadMembershipRepository

        try:
            async with self._sf() as session:
                membership = await ThreadMembershipRepository(session).get(thread_id, agent_user_id)
                watermark = (membership.last_read_block_id or 0) if membership is not None else 0
                blocks = await BlockRepository(session).messages_since(thread_id, watermark)
                backlog = [
                    b
                    for b in blocks
                    if b.id <= block_id and b.deleted_at is None and b.author_id != agent_user_id
                ]
                if not backlog:
                    return [(fallback_speaker, fallback_text)], False
                truncated = len(backlog) > self._UNREAD_CAP
                backlog = backlog[-self._UNREAD_CAP :]
                author_ids = list({b.author_id for b in backlog})
                members = await build_member_dicts(session, self._hub, author_ids)
                names = {int(m["user_id"]): str(m["nickname"]) for m in members}  # type: ignore[call-overload]
                messages = [
                    (names.get(b.author_id, f"user{b.author_id}"), b.content) for b in backlog
                ]
                return messages, truncated
        except Exception:  # backlog assembly must never break delivery
            logging.getLogger(__name__).warning(
                "failed to collect unread backlog (thread=%s agent=%s block=%s)",
                thread_id,
                agent_user_id,
                block_id,
                exc_info=True,
            )
            return [(fallback_speaker, fallback_text)], False

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
        messages, truncated = await self._collect_unread(
            thread_id=thread_id,
            agent_user_id=agent_user_id,
            block_id=block_id,
            fallback_speaker=speaker,
            fallback_text=text,
        )
        prompt = self._thread_prompt(
            thread_id=thread_id, messages=messages, mentioned=mentioned, truncated=truncated
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
          * @mentioned  → always delivered now (ignores policy);
          * ALL         → always delivered now (真正的「立即」);
          * INTERVAL(n) → delivered iff ≥ n minutes since its last delivery here;
          * MENTION     → only when @mentioned.

        Triage (BASIC — no reminder / timeout-escalation): 只对 INTERVAL 广播降噪。
        @mentioned 与 ALL 的 agent 都立即唤醒（ALL 语义就是「每条都立即给我」，用户
        既已显式开启就不该被 triage 抑制）。在剩下的 *非 @* 的 INTERVAL 合格 agent 中
        只立即唤醒最高 role 的一个，其余 defer ~30s（``finish_triage`` 可提前释放）。
        Returns how many agents were (or will be) forwarded to."""
        now = datetime.now(UTC)
        # Basic version: a new message flushes any still-pending deferral for the thread.
        await self._flush_triage(thread_id)

        immediate: list[tuple[int, bool]] = []  # (agent_user_id, mentioned)
        broadcast_eligible: list[ThreadAgentPolicy] = []  # 非 @ 的 INTERVAL 合格者，参与降噪
        for p in agent_policies:
            if p.user_id in mentioned_ids:
                immediate.append((p.user_id, True))
                continue
            if p.mode == "ALL":
                # ALL = 真正的「立即」：不参与 triage 的「只唤醒最高 role 一个」降噪，
                # 始终立即投递，避免非最高 role 的 ALL agent 被误 defer ~30s。
                immediate.append((p.user_id, False))
            elif p.mode == "INTERVAL":
                last = self._last_delivery.get((thread_id, p.user_id))
                if last is None or (now - last) >= timedelta(
                    minutes=p.interval_minutes or 0
                ):
                    broadcast_eligible.append(p)
            # else MENTION: 非 @ 不投

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
        has on this device — the tmux sessions survived the connection drop.

        Each re-adopted screen is then re-provisioned with an ``adopt``
        ``session.create`` carrying the CURRENT cheeselet source: a *fresh* client
        process (after a ``cheese update`` re-exec) re-drives the surviving tmux
        session from it, while a same-process reconnect treats it as a driver
        hot-reload — so a driver fix reaches already-running agents, and a re-exec'd
        client re-adopts its tasks. Without it a fresh client would never re-drive
        the surviving tmux, and a live screen would keep forever the driver it was
        opened with."""
        source = self._cheeselet() if callable(self._cheeselet) else self._cheeselet
        env: dict[str, str] = {}
        if self._agent_api_base:
            env["CHEESE_API"] = self._agent_api_base
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(AgentScreenRow).where(AgentScreenRow.device_id == device_id)
                )
            ).scalars()
            adopted = 0
            for row in rows:
                # Register the screen in the hub if it isn't already. It WILL already
                # be there after a client-side `cheese update` re-exec (the server
                # never restarted, so detach_device only nulled the transport and left
                # _screens intact); it will be absent after a *server* restart. Both
                # cases must still re-provision below.
                if self._hub.screen(row.sid) is None:
                    self._hub.adopt_screen(
                        sid=row.sid,
                        device_id=row.device_id,
                        token=row.token,
                        project_id=row.project_id,
                        agent_user_id=row.agent_user_id,
                    )
                # ALWAYS send the adopt session.create, regardless of whether the hub
                # already knew this screen. After a `cheese update` re-exec the fresh
                # client process starts with an EMPTY local sessions map and needs this
                # message to re-adopt its surviving tmux session — even though the
                # server-side hub state persisted across the client's reconnect. It is
                # idempotent: a same-process reconnect treats it as a driver hot-reload.
                command = (
                    self._claude_command(session_id=row.claude_session_id, cwd=row.cwd, resume=True)
                    if row.claude_session_id
                    else self._command  # legacy row with no recorded session — best effort
                )
                try:
                    await self._hub.readopt_screen(
                        device_id, row.sid, command, source, env=env or None
                    )
                except Exception:
                    logging.getLogger(__name__).warning(
                        "screen re-provision failed for screen %s", row.sid, exc_info=True
                    )
                adopted += 1
        return adopted
