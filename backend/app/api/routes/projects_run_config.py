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
than re-exported through projects.py. `DbSession` comes from there as its alias
for the session dependency. Project reads and settings writes go through
`ProjectService`, which owns persistence rather than exposing a repository
through a sibling route.
The imports the block alone used (`ProjectDefaultModelUpdate`, the
`compute_configs` triple, the `market` triple, `HostPool`, `gate` and
`model_choices`) left projects.py with it; ruff's F401 is what found the
complete list.

The block reads no domain model or repository module; project persistence stays
behind the domain service. No new frozen edge is needed.

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
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.member_settings import change_project_settings
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import Can, InProject
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.core.turn_ceiling import (
    TURN_CEILING_MAX,
    TURN_CEILING_MIN,
    max_concurrent_turns_for,
    project_max_concurrent_turns,
)
from app.domain.agent.environment import (
    default_environment,
    dump,
    parse,
    project_default,
)
from app.domain.agent.model_catalog import model_choices
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine.environments import check_allowed, offered_specs
from app.domain.machine.session_reports import device_sessions, project_distribution
from app.domain.membership.services import MemberService
from app.domain.policy import gate
from app.domain.project.schemas import ProjectDefaultModelUpdate
from app.domain.project.services import ProjectService
from app.domain.project_permissions import Permission
from app.domain.usage.model_access import ModelAccess
from app.domain.usage.services import UsageService

router = APIRouter(prefix="/projects", tags=["projects"])


# --- Project main and native subagent model defaults ---


def _default_model_state(project_settings: dict | None, access: ModelAccess) -> dict:

    # Each model says whether the team's plan allows it and, when not, which
    # plan would; the picker shows that plan and does not offer the model. A
    # model that needs an administrator's approval the team lacks is not listed.
    choices = access.offered(model_choices(project_settings))
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
    }


@router.get("/{project_id}/default-model")
async def get_default_model(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get(project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    access = await UsageService(db).model_access(project.team_id)
    return ok(_default_model_state(project.settings, access))


@router.put("/{project_id}/default-model")
async def save_default_model(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: ProjectDefaultModelUpdate,
    db: DbSession,
) -> dict:
    """设/清项目默认模型。设一个目录里没有的名字直接拒，不静默换池（I27）。
    方案不允许的模型照样拒（``member_settings``）。"""
    project = at.project
    access = await UsageService(db).model_access(project.team_id)
    valid = {c["id"]: c for c in model_choices(project.settings)}
    patch: dict[str, object] = {}
    remove: list[str] = []
    for field, key in (
        ("model", "default_model"),
        ("subagent_model", "default_subagent_model"),
    ):
        if field not in body.model_fields_set:
            continue
        chosen = getattr(body, field)
        if chosen is None:
            remove.append(key)
        elif chosen not in valid or not access.shows(valid[chosen]):
            raise ValidationError(say("modelUnavailableNamed", model=repr(chosen)))
        elif not access.allows(valid[chosen]):
            raise ValidationError(say("modelNotInPlan", label=valid[chosen]["label"]))
        else:
            patch[key] = chosen
    settings = await change_project_settings(
        db, project, patch, remove=remove, by=at.actor.handle
    )
    return ok(_default_model_state(settings, access))


# --- Execution environment: what a new agent in this project gets ---


def _turn_ceiling(project_settings: dict | None) -> dict:
    """How many of this project's turns may run at once, and where that number
    came from — ``project`` when the project chose one, ``deployment`` when it
    follows the deployment default. The same shape as a conversation's
    environment (``default`` + ``default_from``), because it is the same
    question about a different limit."""
    own = project_max_concurrent_turns(project_settings)
    return {
        "max_concurrent_turns": max_concurrent_turns_for(project_settings),
        "max_concurrent_turns_from": "deployment" if own is None else "project",
    }


@router.get("/{project_id}/compute-configs")
async def get_compute_configs(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The environment a new agent in this project gets, where its agents
    are now, the sandbox specs it is offered and the people's computers it
    has, and how many of its turns may run at once."""
    # deferred-import: tests replace this name on app.domain.agent.device_hub
    from app.domain.agent.device_hub import device_hub

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get(project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    # Whether this caller may move the agents already on a device elsewhere
    # (``list_device_sessions``), which stays a manager's. The default itself
    # is anyone's in the project.
    can_manage = True
    try:
        await MemberService(db).require(project_id, actor, Permission.edit_settings)
    except ForbiddenError:
        can_manage = False
    devices = [
        device
        for device in await sql_device_service(db).list_devices_for_project(project_id)
        if device.supply is Supply.self_hosted
    ]
    specs = await offered_specs(db, project)
    labels = {spec["spec"]: spec["label"] for spec in specs}
    distribution = await project_distribution(db, project_id)
    return ok(
        {
            "default": dump(project_default(project.settings) or default_environment()),
            **_turn_ceiling(project.settings),
            "can_manage": can_manage,
            "devices": [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "online": device_hub.is_online(d.device_id),
                    "owned": actor.via == "token"
                    and actor.user_id is not None
                    and await sql_device_service(db).may_manage(d, actor.user_id),
                }
                for d in devices
            ],
            "specs": specs,
            # Where the project's agents are now; the default only decides for
            # agents that have not needed an environment yet.
            "distribution": {
                "sandboxes": [
                    {**entry, "label": labels.get(entry["spec"], entry["spec"])}
                    for entry in distribution["sandboxes"]
                ],
                "devices": distribution["devices"],
            },
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

    Each switch then goes through the session's own route, with its checks. A
    session in a room the caller cannot open is counted in ``hidden`` and not
    listed, so its room's title stays in that room.
    """

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require(project_id, actor, Permission.edit_settings)
    placed = await device_sessions(db, project_id, device_id)
    readable = await resolver.readable_topic_ids(
        actor, project_id=project_id, topics=[topic for topic, _ in placed]
    )
    listed = [session for topic, session in placed if topic.id in readable]
    hidden = len(placed) - len(listed)
    return ok({"sessions": listed, "hidden": hidden})


class ComputeConfigsIn(BaseModel):
    """Either half of the config, so a caller can move one without the other:
    omitted fields stay as they are. ``max_concurrent_turns: null`` clears the
    project's own ceiling, so the project follows the deployment default
    again."""

    default: dict | None = None
    # ``strict``: a JSON ``true`` is an ``int`` in Python, and taking it as one
    # throttles the project to a single turn at a time. A number stays a number.
    max_concurrent_turns: int | None = Field(default=None, strict=True)


@router.put("/{project_id}/compute-configs")
async def save_compute_configs(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: ComputeConfigsIn,
    db: DbSession,
) -> dict:
    """What a new agent in this project gets, and how many of its turns may run
    at once (``member_settings``); nobody already working moves."""
    project = at.project
    patch: dict[str, object] = {}
    remove: list[str] = []
    if "default" in body.model_fields_set and body.default is not None:
        environment = parse(body.default)
        await check_allowed(db, project, environment, at.actor)
        patch["execution_environment"] = environment.model_dump()
    if "max_concurrent_turns" in body.model_fields_set:
        ceiling = body.max_concurrent_turns
        if ceiling is None:
            remove.append("max_concurrent_turns")
        elif not TURN_CEILING_MIN <= ceiling <= TURN_CEILING_MAX:
            raise UnprocessableEntityError(
                say(
                    "maxConcurrentTurnsInvalid",
                    min=TURN_CEILING_MIN,
                    max=TURN_CEILING_MAX,
                )
            )
        else:
            patch["max_concurrent_turns"] = ceiling
    settings_now = await change_project_settings(
        db, project, patch, remove=remove, by=at.actor.handle
    )
    return ok(
        {
            "default": dump(project_default(settings_now) or default_environment()),
            **_turn_ceiling(settings_now),
        }
    )


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
    return ok(_tier_policy(await ProjectService(db).get_or_404(project_id)))


def _tier_policy(project) -> dict:
    policy = gate.policy_of(project.settings)
    return {
        gate.ALLOWED_TIERS_KEY: (
            None if policy.allowed_tiers is None else sorted(policy.allowed_tiers)
        ),
        gate.OVER_TIER_KEY: policy.over_tier,
        "tiers": sorted({choice["tier"] for choice in model_choices(project.settings)}),
    }


@router.put("/{project_id}/tier-policy")
async def set_tier_policy(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: dict,
    db: DbSession,
) -> dict:
    """改档位策略。只有项目管理者能改——它决定谁的点头才能放行一次调用。

    `allowed_tiers` 传 `null` 是不限档（默认），传一个列表是只有这几档可以自己
    发生。身上带的档位名不做存在性校验：目录里的档位随部署变（接一个新池就多一
    档），而一条指向不存在档位的策略只是更严，不会让任何调用悄悄放行。
    """
    project_id = at.project.id
    await MemberService(db).require(project_id, at.actor, Permission.edit_settings)
    service = ProjectService(db)
    project = at.project
    patch: dict[str, object] = {}
    remove: list[str] = []
    if gate.ALLOWED_TIERS_KEY in body:
        tiers = body.get(gate.ALLOWED_TIERS_KEY)
        if tiers is None:
            remove.append(gate.ALLOWED_TIERS_KEY)
        elif isinstance(tiers, list) and all(isinstance(t, str) for t in tiers):
            patch[gate.ALLOWED_TIERS_KEY] = sorted({t.strip() for t in tiers if t})
        else:
            raise ValidationError(say("allowedTiersInvalid"))
    if gate.OVER_TIER_KEY in body:
        disposition = body.get(gate.OVER_TIER_KEY)
        if disposition not in gate.DISPOSITIONS:
            raise ValidationError(
                say("overTierInvalid", values=str(sorted(gate.DISPOSITIONS)))
            )
        patch[gate.OVER_TIER_KEY] = disposition
    await service.merge_settings(project, patch, remove=remove)
    return ok(_tier_policy(project))
