"""Who works in a sandbox, and which sandbox a session works in.

A sandbox's home is named by the environment it backs
(``Environment.sandbox_home_id``), and the sessions working in that
environment are attached to it. So the sessions using a home are the ones
attached to the environment that names it, and the home of a session is the
one its environment names. Everything in ``machine`` that asks either goes
through here: the pool, the idle sweep, the meter and the pages that show a
home never read who uses a home off the home itself.
"""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.agent.models import AgentTurn
from app.domain.agent_session.models import AgentSession
from app.domain.environment.models import Environment, EnvironmentAttachment
from app.domain.machine.models import SandboxHome


def environment_of_home(home_id: Any) -> ColumnElement[uuid.UUID]:
    """The environment that names the home ``home_id`` (a value or a
    column), as a scalar subquery; NULL for a home no environment names (one
    left to the idle sweep)."""
    return (
        select(Environment.id)
        .where(Environment.sandbox_home_id == home_id)
        .limit(1)
        .scalar_subquery()
    )


def sessions_in(home_id: Any):
    """The ids of the sessions attached to the environment that names the
    home ``home_id`` (a value or a column), as a selectable."""
    return (
        select(EnvironmentAttachment.session_id)
        .join(Environment, Environment.id == EnvironmentAttachment.environment_id)
        .where(
            Environment.sandbox_home_id == home_id,
            EnvironmentAttachment.session_id.is_not(None),
        )
    )


def turns_in(home_id: Any) -> ColumnElement[bool]:
    """A turn of one of the sessions working in the home ``home_id``, for a
    query over ``agent_turns``: one in the conversation a session there
    works in, under its own seat. The conversation is the session's own,
    never its room's — every task of a channel hangs under the room — and a
    turn whose seat was never recorded counts as anybody's, which is how every
    other reader counts one (``agent.turn.state.authority``)."""
    session = aliased(AgentSession)
    return (
        exists()
        .where(
            EnvironmentAttachment.session_id == session.id,
            Environment.id == EnvironmentAttachment.environment_id,
            Environment.sandbox_home_id == home_id,
            AgentTurn.conversation_id == session.conversation_id,
            or_(
                AgentTurn.agent_handle.is_(None),
                AgentTurn.agent_handle == session.agent_handle,
            ),
        )
        .correlate_except(EnvironmentAttachment, Environment, session)
    )


def a_session_in(home_id: Any) -> ColumnElement[uuid.UUID]:
    """One session working in the home ``home_id``, the one attached longest,
    as a scalar subquery: what a record of the home names as its session.
    NULL for a home nobody works in."""
    return (
        select(EnvironmentAttachment.session_id)
        .join(Environment, Environment.id == EnvironmentAttachment.environment_id)
        .where(
            Environment.sandbox_home_id == home_id,
            EnvironmentAttachment.session_id.is_not(None),
        )
        .order_by(EnvironmentAttachment.acted_at, EnvironmentAttachment.session_id)
        .limit(1)
        .scalar_subquery()
    )


async def sessions_of(
    db: AsyncSession, home_id: uuid.UUID, *, lock: bool = False
) -> list[AgentSession]:
    """The sessions working in the home, attached longest first; with
    ``lock`` their rows are taken, for a caller that writes what they are
    owed."""
    query = (
        select(AgentSession)
        .join(
            EnvironmentAttachment, EnvironmentAttachment.session_id == AgentSession.id
        )
        .join(Environment, Environment.id == EnvironmentAttachment.environment_id)
        .where(Environment.sandbox_home_id == home_id)
        .order_by(EnvironmentAttachment.acted_at, AgentSession.id)
    )
    if lock:
        query = query.with_for_update(of=AgentSession).execution_options(
            populate_existing=True
        )
    return list(await db.scalars(query))


async def environment_named(db: AsyncSession, home_id: uuid.UUID) -> uuid.UUID | None:
    """The environment that names the home; None when none does."""
    return await db.scalar(select(environment_of_home(home_id)))


async def home_of_environment(
    db: AsyncSession, environment_id: uuid.UUID, *, lock: bool = False
) -> SandboxHome | None:
    """The home the environment names, as the database has it now: a sweep
    may have started destroying it since this was last read."""
    query = (
        select(SandboxHome)
        .join(Environment, Environment.sandbox_home_id == SandboxHome.id)
        .where(Environment.id == environment_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update(of=SandboxHome)
    return await db.scalar(query)


def home_of_session_id(session_id: Any) -> ColumnElement[uuid.UUID]:
    """The home of the environment the session ``session_id`` (a value or a
    column) is attached to, as a scalar subquery."""
    return (
        select(Environment.sandbox_home_id)
        .join(
            EnvironmentAttachment,
            EnvironmentAttachment.environment_id == Environment.id,
        )
        .where(EnvironmentAttachment.session_id == session_id)
        .scalar_subquery()
    )


async def home_of(db: AsyncSession, session_id: uuid.UUID) -> SandboxHome | None:
    """The home of the environment the session is attached to; None while it
    is attached to none, or its environment has no sandbox."""
    return await db.scalar(
        select(SandboxHome)
        .where(SandboxHome.id == home_of_session_id(session_id))
        .execution_options(populate_existing=True)
    )


async def homes_of(db: AsyncSession, session_ids) -> dict[uuid.UUID, SandboxHome]:
    """The home each of these sessions works in, by session; one in no
    sandbox is absent."""
    wanted = [one for one in session_ids if one is not None]
    if not wanted:
        return {}
    rows = await db.execute(
        select(EnvironmentAttachment.session_id, SandboxHome)
        .join(Environment, Environment.id == EnvironmentAttachment.environment_id)
        .join(SandboxHome, SandboxHome.id == Environment.sandbox_home_id)
        .where(EnvironmentAttachment.session_id.in_(wanted))
    )
    return {session_id: home for session_id, home in rows.all()}


def in_use(home_id: Any) -> ColumnElement[bool]:
    """A home some session works in: an environment names it and somebody is
    attached to that environment."""
    return (
        exists()
        .where(
            Environment.id == EnvironmentAttachment.environment_id,
            Environment.sandbox_home_id == home_id,
        )
        .correlate_except(EnvironmentAttachment, Environment)
    )


def homes_in_conversation(conversation_id: Any):
    """The ids of the homes the sessions of a conversation work in, as a
    selectable."""
    return (
        select(Environment.sandbox_home_id)
        .join(
            EnvironmentAttachment,
            EnvironmentAttachment.environment_id == Environment.id,
        )
        .join(AgentSession, AgentSession.id == EnvironmentAttachment.session_id)
        .where(
            AgentSession.conversation_id == conversation_id,
            Environment.sandbox_home_id.is_not(None),
        )
    )


def sessions_with_homes():
    """Each session with the home it works in, as a selectable of
    ``(session_id, SandboxHome)`` for a caller to filter."""
    return (
        select(AgentSession.id, SandboxHome)
        .join(
            EnvironmentAttachment, EnvironmentAttachment.session_id == AgentSession.id
        )
        .join(Environment, Environment.id == EnvironmentAttachment.environment_id)
        .join(SandboxHome, SandboxHome.id == Environment.sandbox_home_id)
    )
