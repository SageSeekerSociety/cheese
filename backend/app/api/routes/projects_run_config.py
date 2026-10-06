"""A project's run config: which model, which machine, and which tiers may run.

Part of #2143. Second slice of `app/api/routes/projects.py`, following the same
pattern #2198/#2204/#2208/#2214 took for `spaces.py`, #2215 for `topics.py` and
the first slice of this file, `projects_artifacts.py`. projects.py is 1,403
lines against a 1,500-line cap that only ratchets down.

What moves, verbatim:

  GET    /projects/{project_id}/default-model
  PUT    /projects/{project_id}/default-model
  GET    /projects/{project_id}/compute-configs
  PUT    /projects/{project_id}/compute-configs
  GET    /projects/{project_id}/devices/{device_id}/sessions
  GET    /projects/{project_id}/tier-policy
  PUT    /projects/{project_id}/tier-policy

and the helper only they call: `_default_model_state` (what a project's default
model is, read the one way both of the routes below need it, plus the pool the
discovery layer filters its catalog by). They are one group because they answer
one question about one project -- what a run of it gets. Which model is
`default-model`; which machine is `compute-configs`, with the sessions already
sitting on each device next to it; and which tiers may happen without a person
saying yes is `tier-policy`, which is also what `domain/policy/gate.py` reads.

What stays behind, and why. `ProjectService`, `MemberService`, `ok`,
`ActorResolverDep`, `settings` and the error types are read by handlers that
stay, so each is imported here from the module that owns it
(`app.domain.project.services`, `app.domain.membership.services`,
`app.api.response`, `app.api.auth`, `app.core.config`, `app.core.errors`) rather
than re-exported through projects.py. Two names do come from there: `DbSession`
is projects.py's own alias for the session dependency, and `ProjectRepository`
is a repository the handlers that stay also read -- and the one the guard in
`tests/unit/test_domain_import_guard.py` freezes under projects.py -- so it is
taken from there the way `topics_compute.py` takes its own pair from topics.py.
The imports the block alone used (`ProjectDefaultModelUpdate`, the
`compute_configs` triple, the `market` triple, `HostPool`, `gate` and
`model_choices`) left projects.py with it; ruff's F401 is what found the
complete list.

No frozen edge moves and no contract grows. The block reads no domain *model*
module -- C2 freezes `app.api.routes.projects -> app.domain.*.models`, and no
import of that shape is in it -- and no *repository* module directly: it reaches
`ProjectRepository` through projects.py, so the pair the guard freezes stays
exactly where it was. `.importlinter` is untouched.

Ordering. This module sorts after `projects_artifacts.py` (`projects_a` <
`projects_r`) and before `push.py`, so its paths mount later in the route table
than they did inside projects.py -- no longer between `/forge-attribution` and
`/task-naming`, but after every path that stays plus every artifact path, up to
`PUT /projects/{project_id}/upstream`. Every moved path carries a literal
(`default-model`, `compute-configs`, `devices`/`sessions`, `tier-policy`) where
each route that now precedes it carries a literal of its own, and no route
registered in between has a parameter in that segment, so none loses its first
full match; resolving every path in the table confirms each still reaches the
handler it did before, now under `app.api.routes.projects_run_config`. OpenAPI
is byte-identical apart from the moved paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.projects import DbSession, ProjectRepository
from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.compute_configs import (
    ProjectComputeConfigs,
    project_configs,
    validate_choice,
)
from app.domain.agent.market import (
    COMPUTE_CLOUD,
    COMPUTE_TIERS,
    cloud_vm_provisionable,
    compute_selectable,
)
from app.domain.agent_instance.configuration import model_choices
from app.domain.machine.services import HostPool
from app.domain.membership.services import MemberService
from app.domain.policy import gate
from app.domain.project.schemas import ProjectDefaultModelUpdate
from app.domain.project.services import ProjectService
from app.domain.usage.model_access import ModelAccess
from app.domain.usage.services import UsageService

router = APIRouter(prefix="/projects", tags=["projects"])


# --- Project main and native subagent model defaults ---


def _default_model_state(project_settings: dict | None, access: ModelAccess) -> dict:
    from app.domain.agent_instance.configuration import model_choices, project_pool

    # Each model says whether the team's plan allows it and, when not, which
    # plan would; the picker shows that plan and does not offer the model.
    choices = [access.mark(c) for c in model_choices(project_settings)]
    chosen = (project_settings or {}).get("default_model")
    # The saved name goes back as saved, even when it has left the catalog: turns
    # refuse it (binding.resolve) rather than fall back to the deployment default,
    # so the picker must show it as unavailable, not show the default in its place.
    deployment_settings = dict(project_settings or {})
    deployment_settings.pop("default_model", None)
    deployment_choices = model_choices(deployment_settings)
    return {
        "model": chosen if isinstance(chosen, str) and chosen else None,
        "subagent_model": (project_settings or {}).get("default_subagent_model"),
        "deployment_default": next(
            (c["id"] for c in deployment_choices if c["default"]), None
        ),
        "choices": choices,
        # 发现层（sync-agents）按池过滤目录：与准入同源的 project_pool,别让
        # 每个读目录的人自己从默认项反推（零默认的目录推不出来）。
        "pool": project_pool(project_settings),
        "can_manage": False,  # 由路由层按权限填
    }


@router.get("/{project_id}/default-model")
async def get_default_model(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    access = await UsageService(db).model_access(project.team_id)
    state = _default_model_state(project.settings, access)
    try:
        await MemberService(db).require_manager(project_id, actor)
        state["can_manage"] = True
    except ForbiddenError:
        state["can_manage"] = False
    return ok(state)


@router.put("/{project_id}/default-model")
async def save_default_model(
    project_id: uuid.UUID,
    body: ProjectDefaultModelUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """设/清项目默认模型。设一个目录里没有的名字直接拒，不静默换池（I27）。"""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    project = await ProjectService(db).get_or_404(project_id)
    values = dict(project.settings or {})
    from app.domain.agent_instance.configuration import model_choices

    access = await UsageService(db).model_access(project.team_id)
    valid = {c["id"]: c for c in model_choices(values)}
    for field, key in (
        ("model", "default_model"),
        ("subagent_model", "default_subagent_model"),
    ):
        if field not in body.model_fields_set:
            continue
        chosen = getattr(body, field)
        if chosen is None:
            values.pop(key, None)
        elif chosen not in valid:
            raise ValidationError(say("modelUnavailableNamed", model=repr(chosen)))
        elif not access.allows(valid[chosen]["tier"]):
            raise ValidationError(say("modelNotInPlan", label=valid[chosen]["label"]))
        else:
            values[key] = chosen
    project.settings = values
    await db.flush()
    state = _default_model_state(project.settings, access)
    state["can_manage"] = True
    return ok(state)


# --- Compute pool (design §3): which machine runs this project's sandbox ---


@router.get("/{project_id}/compute-configs")
async def get_compute_configs(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    from app.domain.agent.device_hub import device_hub
    from app.domain.device.wiring import sql_device_service

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    can_manage = True
    try:
        await MemberService(db).require_manager(project_id, actor)
    except ForbiddenError:
        can_manage = False
    devices = await sql_device_service(db).list_devices_for_project(project_id)
    from app.domain.machine.session_reports import project_distribution

    return ok(
        {
            **project_configs(project.settings).model_dump(),
            "can_manage": can_manage,
            # Where the project's agents are working now; the default only
            # decides for agents that have not started.
            "distribution": await project_distribution(db, project_id),
            "devices": [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "online": device_hub.is_online(d.device_id),
                }
                for d in devices
            ],
            "cloud_available": any(
                p.id == COMPUTE_CLOUD for p in compute_selectable(settings)
            ),
            "cloud_vm_available": cloud_vm_provisionable(settings),
        }
    )


@router.get("/{project_id}/devices/{device_id}/sessions")
async def list_device_sessions(
    project_id: uuid.UUID,
    device_id: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """The project's agent sessions on one self-hosted device, for a project
    manager to switch some of them elsewhere (「现在的分布」 → 查看并更换).

    Each switch then goes through the room's own route, with its checks. A
    session in a room the caller cannot open is counted in ``hidden`` and not
    listed, so its room's title stays in that room.
    """
    from app.domain.machine.session_reports import device_sessions

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    listed, hidden = [], 0
    for topic, session in await device_sessions(db, project_id, device_id):
        try:
            await resolver.authorize_topic(
                actor, project_id=project_id, topic_id=topic.id
            )
        except ForbiddenError:
            hidden += 1
            continue
        listed.append(session)
    return ok({"sessions": listed, "hidden": hidden})


@router.put("/{project_id}/compute-configs")
async def save_compute_configs(
    project_id: uuid.UUID,
    body: ProjectComputeConfigs,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    await MemberService(db).require_manager(project_id, actor)
    await validate_choice(db, project_id, body.default)
    await HostPool(db).admit_choice(project_id, actor, body.default)
    values = dict(project.settings or {})
    values.pop("compute_profile", None)
    values["compute_configs"] = body.model_dump()
    project.settings = values
    await db.flush()
    return ok(body.model_dump())


# --- 档位策略 (结论 3 后半 / 40 后半): 哪几档可以自己发生，超档怎么办 -----


@router.get("/{project_id}/tier-policy")
async def get_tier_policy(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这个项目允许哪几档资源自己发生，以及超档怎么办。

    `tiers` 一并给出目录里现在存在的档位，所以调用方不必自己维护一份档位表——
    那正是闸门拒绝按型号列白名单的同一个理由（`domain/policy/gate.py`）。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    policy = gate.policy_of(project.settings)
    return ok(
        {
            gate.ALLOWED_TIERS_KEY: (
                None if policy.allowed_tiers is None else sorted(policy.allowed_tiers)
            ),
            gate.OVER_TIER_KEY: policy.over_tier,
            "tiers": sorted(
                {choice["tier"] for choice in model_choices(project.settings)}
                | set(COMPUTE_TIERS.values())
            ),
        }
    )


@router.put("/{project_id}/tier-policy")
async def set_tier_policy(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """改档位策略。只有项目管理者能改——它决定谁的点头才能放行一次调用。

    `allowed_tiers` 传 `null` 是不限档（默认），传一个列表是只有这几档可以自己
    发生。身上带的档位名不做存在性校验：目录里的档位随部署变（接一个新池就多一
    档），而一条指向不存在档位的策略只是更严，不会让任何调用悄悄放行。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    project = await ProjectService(db).get_or_404(project_id)
    values = dict(project.settings or {})
    if gate.ALLOWED_TIERS_KEY in body:
        tiers = body.get(gate.ALLOWED_TIERS_KEY)
        if tiers is None:
            values.pop(gate.ALLOWED_TIERS_KEY, None)
        elif isinstance(tiers, list) and all(isinstance(t, str) for t in tiers):
            values[gate.ALLOWED_TIERS_KEY] = sorted({t.strip() for t in tiers if t})
        else:
            raise ValidationError(say("allowedTiersInvalid"))
    if gate.OVER_TIER_KEY in body:
        disposition = body.get(gate.OVER_TIER_KEY)
        if disposition not in gate.DISPOSITIONS:
            raise ValidationError(
                say("overTierInvalid", values=str(sorted(gate.DISPOSITIONS)))
            )
        values[gate.OVER_TIER_KEY] = disposition
    project.settings = values
    await db.flush()
    return await get_tier_policy(project_id, db, resolver)
