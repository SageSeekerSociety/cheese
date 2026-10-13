"""A project's agents: the ones it keeps, and which of them new rooms start with.

Part of #2143. Third slice of `app/api/routes/projects.py`, following the same
pattern #2198/#2204/#2208/#2214 took for `spaces.py`, #2215 for `topics.py` and
the first two slices of this file, `projects_artifacts.py` (#2221) and
`projects_run_config.py` (#2226). projects.py is 1,143 lines against a
1,500-line cap that only ratchets down.

What moves, verbatim:

  GET    /projects/{project_id}/agents
  POST   /projects/{project_id}/agents
  PUT    /projects/{project_id}/agents/{agent_id}
  DELETE /projects/{project_id}/agents/{agent_id}
  PUT    /projects/{project_id}/default-agent

and the two helpers only they call: `_holds_the_default` (whether a row is the
one a new room in this project gets) and `_agent_out` (the one wire payload all
five return). They are one group because they are one subject read one way --
the project's roster of agents: what it has saved, adding one, editing one's
name and configuration, retiring one, and picking which of them a new room
starts with. `default-agent` belongs here rather than with the run config
because it points at a row in this list: it is a choice among the agents, not a
property of a run.

What stays behind, and why. `ProjectService`, `ok`/`page`, `ActorResolverDep`
and the error types are read by handlers that stay, so each is imported here
from the module that owns it (`app.domain.project.services`, `app.api.response`,
`app.api.auth`, `app.core.errors`) rather than re-exported through projects.py.
Two names do come from there. `DbSession` is projects.py's own alias for the
session dependency, taken the way `projects_artifacts.py` and
`projects_run_config.py` take their own. `Project` is the model the
`_holds_the_default` annotation names, and it too is imported from projects.py
rather than from `app.domain.project.models` on purpose -- the shape
`topics_title.py` uses for `Topic`, and for the same reason: C2 ratchets
(route module, model module) pairs, and projects.py still reads `Project`
everywhere, so that line stays matched and this move adds no exemption to any
boundary. Five imports left projects.py with the block (`AgentInstance`,
`AgentInstanceCreate`, `AgentInstanceOut`, `AgentInstanceUpdate`,
`ProjectDefaultAgentIn`, `AgentInstanceService`, `ResolvedAgent`,
`AgentConfiguration` and `agent_instance_handle`); ruff's F401 is what found the
complete list. `ANONYMOUS_HANDLE` came from the same line as
`agent_instance_handle` and stays, because a handler that stays reads it.

One frozen edge moves with the code, and nothing grows. C2 freezes
`app.api.routes.projects -> app.domain.agent_instance.models`, and
`_holds_the_default`'s `row: AgentInstance` annotation was the only reader of
that module in projects.py. The edge therefore leaves `app.api.routes.projects`
and is frozen under `app.api.routes.projects_agents` -- the same debt with a new
importer, not new debt, exactly the shape `topics_tasks.py` took for
`app.domain.notification.models`. One line changed in `.importlinter`, none was
added, and no contract grew. No *repository* module is imported here at all, so
the four pairs `tests/unit/test_domain_import_guard.py` freezes against
`app.api.routes.projects` stay exactly where they were.

Ordering. This module sorts after `projects.py` (`_` > `.`) and before
`projects_artifacts.py` (`projects_ag` < `projects_ar`), so its paths mount later
in the route table than they did inside projects.py -- no longer right after
`GET /projects/{project_id}`, but after every path that stays,
up to `PUT /projects/{project_id}/upstream`, and before every artifact path.
Every moved path carries a literal (`agents`, `default-agent`) where each route
that now precedes it carries a literal of its own, and no route registered in
between has a parameter in that segment, so none loses its first full match;
resolving every path in the table confirms each still reaches the handler it did
before, now under `app.api.routes.projects_agents`. OpenAPI is byte-identical
apart from the moved paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import asyncio
import uuid
from typing import Protocol

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, page, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ConflictError
from app.core.sentences import say
from app.domain.agent_instance import editing
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.own import owned_instance
from app.domain.agent_instance.schemas import (
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstanceUpdate,
    ProjectDefaultAgentIn,
)
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
    initial_configuration,
)
from app.domain.identity.actor import Actor
from app.domain.identity.handles import agent_instance_handle
from app.domain.membership.services import MemberService
from app.domain.project.services import ProjectService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/projects", tags=["projects"])


def _changes_what_it_costs(saved: dict, config: AgentConfiguration) -> bool:
    """Whether this save touches a cost knob only a project manager may set.

    The settings page keeps them under 「高级设置」: the highest effort, and when
    the context is compacted. Both decide what every turn of the teammate costs,
    as the project main model does, and that one is a manager's too
    (``require_manager`` in ``projects_run_config``).
    """
    return (
        config.effort == "max" and saved.get("effort") != "max"
    ) or config.compact_percent != saved.get("compact_percent")


def _changes_its_skills(saved: dict, config: AgentConfiguration) -> bool:
    """Whether this save changes which skills the teammate carries.

    The third of the 「高级设置」, and the one that is NOT a manager's alone: it
    is the project's (``_may_configure``). A save that leaves it as it was is
    anyone's, so a member renaming a teammate or rewriting its role is not
    refused for what it did not touch.
    """
    return config.skills != list(saved.get("skills") or [])


def _changes_an_advanced_setting(saved: dict, config: AgentConfiguration) -> bool:
    """Either of the above — what creating a teammate is judged by.

    Creation has no instance yet, so it cannot ask ``_may_configure``; a new
    teammate is seeded by a manager or not at all.
    """
    return _changes_what_it_costs(saved, config) or _changes_its_skills(saved, config)


class ProjectDefault(Protocol):
    """What ``_holds_the_default`` reads off a project row.

    ``Mapped``-shaped, not the unwrapped ``uuid.UUID | None``: pyright matches
    the project model's own ``Mapped`` descriptor against this class member, so
    a plain optional UUID here would not accept the model — see ``SpaceRow`` in
    ``routes/spaces.py`` and ``TeamView`` in ``domain/team/protocol.py``.
    """

    default_agent_instance_id: Mapped[uuid.UUID | None]


# --- A project's agents: its roster, and its default for new rooms -----------
def _holds_the_default(project: ProjectDefault, row: AgentInstance) -> bool:
    """Whether this row is what a new topic in the project gets.

    One way to be it: the project points at it. A project is created with its
    芝士 and pointed at it right there, so there is no longer a second way — an
    agent that holds the default before anything points at it.
    """
    return row.id == project.default_agent_instance_id


def _agent_out(
    project_id: uuid.UUID,
    agent: ResolvedAgent,
    *,
    is_default: bool,
    is_active: bool = True,
    editing: str | None = None,
) -> dict:
    return AgentInstanceOut(
        id=agent.instance_id,
        project_id=project_id,
        handle=agent.handle,
        seat_handle=agent_instance_handle(agent.instance_id),
        type_name=agent.type_name,
        display_name=agent.display_name,
        name_source=agent.name_source.value,
        configuration=AgentConfiguration.model_validate(agent.configuration),
        version=agent.version,
        is_default=is_default,
        is_active=is_active,
        editing=editing,
    ).model_dump(mode="json")


async def _may_configure(
    db: AsyncSession, project_id: uuid.UUID, instance: AgentInstance, actor: Actor
) -> bool:
    """Whether ``actor`` may change this agent's skills — three ways, in order.

    It is their **own** agent (#2991): what runs on their machine with their
    login is theirs to arm. Or they **are on the project** — a teammate belongs
    to the project it works in, so the question is the project's own 「谁是项目
    成员」 (`TopicMemberService.on_project`, the roster), the same answer the
    project door and the sidebar already give.

    It used to be 「坐进它坐的那间房」 (`share_a_room_with`). That tied a
    project-level question to rooms, and the two only agreed by accident — they
    part ways when the project has no public room at all (`if not topics:
    return False`), when it has no 综合 to make 「everyone is in it」 true, or
    when two handles' only shared place is a private chat (chats are not part of
    the topic tree). A teammate is not a room's, it is the project's: rooms are
    where work happens, not who a teammate belongs to.

    Or they **manage the project**.
    """
    own = await owned_instance(db, instance.id)
    if own is not None and own.owner_user_id == actor.user_id:
        return True
    if await TopicMemberService(db).on_project(project_id, actor.handle):
        return True
    return await MemberService(db).manages(project_id, actor.handle)


@router.get("/{project_id}/agents")
async def list_project_agents(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's saved agents, including its default for new rooms."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    await service.for_project(project)
    rows = await service.list_team(project_id)
    # Who is editing what, in one round of reads against Valkey (one per agent;
    # a project has a handful). None for every agent when it is not configured.
    editors = await asyncio.gather(*(editing.holder(row.id) for row in rows))
    items = [
        _agent_out(
            project_id,
            AgentInstanceService.resolved(row),
            is_default=_holds_the_default(project, row),
            is_active=row.is_active,
            editing=editor,
        )
        for row, editor in zip(rows, editors, strict=True)
    ]
    return ok(page(items, len(items)))


@router.post("/{project_id}/agents")
async def create_project_agent(
    project_id: uuid.UUID,
    body: AgentInstanceCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Add an agent to this project. It starts with an empty memory pool."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    chosen = body.configuration
    if chosen is not None and _changes_an_advanced_setting(
        initial_configuration(body.type_name).model_dump(), chosen
    ):
        # Measured against the preset it starts from: its skills come with it,
        # so only skills of somebody's own choosing, the highest effort or a
        # compaction share ask for a manager.
        await MemberService(db).require_manager(project_id, actor)
    service = AgentInstanceService(db)
    instance = await service.create(
        project_id=project_id,
        handle=body.handle or f"agent-{uuid.uuid4().hex[:8]}",
        type_name=body.type_name,
        display_name=body.display_name,
        configuration=body.configuration,
    )
    await db.flush()
    return ok(
        _agent_out(
            project_id, AgentInstanceService.resolved(instance), is_default=False
        )
    )


@router.put("/{project_id}/agents/{agent_id}")
async def update_project_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: AgentInstanceUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Edit one agent's name and saved configuration.

    ``handle`` is not editable and is not accepted here: it keys the memory
    pool, so changing it would hand the agent an empty one and orphan
    everything it had learned in this project.
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(project_id=project_id, instance_id=agent_id)
    fields = body.model_fields_set
    if body.configuration is not None:
        saved = instance.configuration or {}
        # Two gates, not one. The cost knobs stay a manager's exactly as before.
        if _changes_what_it_costs(saved, body.configuration):
            await MemberService(db).require_manager(project_id, actor)
        # The skills an agent carries are the project's -- its own agent's owner,
        # anybody on the project, or a manager (``_may_configure``).
        if _changes_its_skills(saved, body.configuration):
            if not await _may_configure(db, project_id, instance, actor):
                await MemberService(db).require_manager(project_id, actor)
    if "display_name" in fields and body.display_name is not None:
        await service.rename(instance, body.display_name)
    if body.configuration is not None:
        await service.configure(
            instance, body.configuration, expected_version=body.expected_version
        )
    await db.flush()
    return ok(
        _agent_out(
            project_id,
            AgentInstanceService.resolved(instance),
            is_default=_holds_the_default(project, instance),
            is_active=instance.is_active,
        )
    )


class AgentEditingIn(BaseModel):
    """Whether the caller is taking or giving up the 「正在编辑」 lease."""

    model_config = ConfigDict(extra="forbid")
    held: bool


class AgentEditingHolder(BaseModel):
    handle: str
    seconds: int


class AgentEditingOut(BaseModel):
    """Who holds the lease, or null once it is given up.

    A refusal is a 409, not this — this body is only ever the answer that the
    lease is the caller's (or nobody's).
    """

    editing: AgentEditingHolder | None


@router.put(
    "/{project_id}/agents/{agent_id}/editing",
    dependencies=[ROUTE_DECIDES],
    **typed_response(AgentEditingOut),
)
async def set_agent_editing(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: AgentEditingIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Take or give up the courtesy lease that says 「我正在改这个队友」.

    Not a lock. What refuses a stale save is the ``version`` on the row; this is
    so the second person hears that the first is already here *before* spending
    time on a change that would be refused. It therefore has to be takeable when
    a screen opens and releasable when it closes, which is why it is its own
    address and not a side effect of a save.

    Whom it admits is ``_may_configure`` — the same three ways that admit a save,
    so it can never say 「你可以编辑」 to somebody a save would refuse.
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(project_id=project_id, instance_id=agent_id)
    if not await _may_configure(db, project_id, instance, actor):
        await MemberService(db).require_manager(project_id, actor)
    if not body.held:
        await editing.released(instance.id, actor.handle)
        # The envelope is the contract: the web transport refuses a 2xx whose
        # body has no ``code`` (``http.ts``), so a bare dict here reads as
        # "someone is editing" to the panel — the lease was fine, the reply
        # shape was not.
        return ok({"editing": None})
    if not await editing.take(instance.id, actor.handle):
        # Somebody else is in it. Naming them is the useful half of the refusal.
        holder = await editing.holder(instance.id)
        raise ConflictError(
            say("agentEditingTaken", handle=holder), data={"handle": holder}
        )
    return ok({"editing": {"handle": actor.handle, "seconds": editing.LEASE_SECONDS}})


@router.delete("/{project_id}/agents/{agent_id}")
async def deactivate_project_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Retire an agent — not a delete.

    The rooms already working with it carry on and its memory is kept; it just
    stops being offered for new work. The response says ``deleted`` because
    that is the shape a DELETE returns everywhere here, not because a row went
    away.
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(project_id=project_id, instance_id=agent_id)
    await service.deactivate(project, instance)
    return ok({"deleted": True})


@router.put("/{project_id}/default-agent")
async def set_project_default_agent(
    project_id: uuid.UUID,
    body: ProjectDefaultAgentIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Select the existing agent that new rooms start with."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    service = AgentInstanceService(db)
    instance = await service.get_in_project(
        project_id=project_id, instance_id=body.instance_id
    )
    agent = await service.set_project_default(project, instance)
    return ok(_agent_out(project_id, agent, is_default=True))
