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

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.api.routes.projects import DbSession, Project
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.schemas import (
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstanceUpdate,
    ProjectDefaultAgentIn,
)
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
)
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.services import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


# --- A project's agents: its roster, and its default for new rooms -----------
def _holds_the_default(project: Project, row: AgentInstance) -> bool:
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
        is_default=is_default,
        is_active=is_active,
    ).model_dump(mode="json")


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
    rows = await service.list_for_project(project_id)
    items = [
        _agent_out(
            project_id,
            AgentInstanceService.resolved(row),
            is_default=_holds_the_default(project, row),
            is_active=row.is_active,
        )
        for row in rows
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
    if "display_name" in fields and body.display_name is not None:
        await service.rename(instance, body.display_name)
    if body.configuration is not None:
        await service.configure(instance, body.configuration)
    await db.flush()
    return ok(
        _agent_out(
            project_id,
            AgentInstanceService.resolved(instance),
            is_default=_holds_the_default(project, instance),
            is_active=instance.is_active,
        )
    )


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
