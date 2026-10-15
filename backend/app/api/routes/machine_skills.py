"""A machine's own skills over HTTP — scanning its disk, adopting what it has.

Five endpoints, all under the owner's own login and all scoped to one of their
own machines, the shape ``local_dirs.py`` set for ``/connector/my/devices``:

* ``GET  /connector/my/devices/{device_id}/skills`` — the index, as the last
  scan left it. No trip to the machine: a screen that opened the list should
  not wait on a laptop.
* ``POST /connector/my/devices/{device_id}/skills/scan`` — go read the machine
  now and record what it has. This is 刷新; it brings skills into the index and
  changes nobody's ``state`` beyond what a scan reads
  (``machine_skill/service.py``), so scanning never adopts anything. A machine
  the person enrolled themselves (``self_hosted``) has its own agent homes
  (``~/.claude`` and the like) left out of the read — those skills are the
  person's own, not this platform's to index.
* ``POST /connector/my/devices/{device_id}/skills/{name}/adopt`` — a person says
  a skill may be offered when a teammate's skills are picked, or takes that back
  (``{"adopted": false}``). The path names the action's home and the body says
  which way: the decision is one field, and a second address would have to be
  pinned in the route index for it.
* ``POST /connector/my/devices/{device_id}/skills/roots`` — record the
  workspaces to scan beside the machine's own homes, then read it again. A
  workspace-scoped skill (``<workspace>/.trae/skills``) exists nowhere else, so
  without this the machine's own home is all anyone can see. The list is kept
  on the machine (``machine_skill/scan.py``), not here.
* ``POST /connector/my/devices/{device_id}/skills/web`` — fetch a skill from a
  GitHub address and land it on the machine, then read it again. The skill is
  checked against the agentskills.io rules (``machine_skill/spec.py``) before
  anything is written, and it lands under ``~/.cheese/web-skills`` where the
  scan finds it like any other.

``scan`` and ``roots`` take an optional ``project_id``: it names the project
whose session homes (``~/.cheese/home/<project>/*/.claude/skills``) are scanned
beside the machine's own directories, so a skill a teammate wrote during a
session of *this* project shows up and can be adopted.

The gate on adopting is owning the machine, not a project's manager. The skill
lives on that person's disk and the body is theirs (see
``machine_skill/models.py``), so 「my computer, my files」 is the tighter and the
right rule — the same one a directory grant already answers to.
``machine_skill/scan.py`` runs on the machine through ``device_hub.exec``, one
one-shot command, the way ``machine/lifecycle.py`` asks a host to work.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    AuthenticationRequiredError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain import skill_market
from app.domain.agent.device_hub import DeviceCallError, device_hub
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.machine_skill import scan, spec
from app.domain.machine_skill.service import MachineSkillService
from app.domain.project_skill.importer import (
    MAX_TOTAL_BYTES,
    normalize_skill_url,
    read_github,
    read_github_many,
)

router = APIRouter(
    prefix="/connector", tags=["machine-skills"], dependencies=[ROUTE_DECIDES]
)

#: How long a machine is given to walk its own home for skills.
SCAN_TIMEOUT_S = 60

#: A person lists a handful of checkouts, not a filesystem.
MAX_WORKSPACE_ROOTS = 20


class SkillRootsRequest(BaseModel):
    """The workspaces to scan beside the machine's own homes."""

    roots: list[str] = Field(default_factory=list, max_length=MAX_WORKSPACE_ROOTS)


class AdoptionRequest(BaseModel):
    """A person's decision about one skill on their machine.

    The body is required, unlike the route it replaced: FastAPI reads a model
    parameter as required unless it is given a default, and a silent default here
    would hide the one thing worth saying — which way the decision went.
    """

    adopted: bool = True


class WebSkillRequest(BaseModel):
    """A skill to fetch from the internet and land on the machine.

    ``kind`` says what the address points at: ``skill`` is one skill at that
    folder; ``plugin`` is a plugin, whose bundled skills (``skills/*/SKILL.md``)
    are each landed. It defaults to the older meaning, so the existing callers
    and the hand-typed address in the dialog keep working untouched.

    ``confirm`` is the gate before writing: left false, the call reads and
    judges everything and answers with what it *would* write, touching nobody's
    disk; only a second call with ``confirm`` true lands it. What lands is a
    script somebody else wrote, running with the caller's own permissions on
    the caller's own machine, so a person gets to see it named first —
    ``claude plugin install`` shows a 「Will install」 list for the same reason.
    """

    url: str
    kind: Literal["skill", "plugin"] = "skill"
    project_id: uuid.UUID | None = None
    confirm: bool = False


class MachineSkillOut(BaseModel):
    """One index row on a machine, as the last scan left it."""

    name: str
    description: str
    source_root: str
    digest: str
    state: str
    adopted: bool


class MachineSkillListOut(BaseModel):
    skills: list[MachineSkillOut]


class SkillScanOut(MachineSkillListOut):
    """A scan's answer: the index plus why it may be short.

    ``problems`` is a directory the spec refused, ``roots`` and ``workspaces``
    are what was actually walked — an empty list is then explained instead of
    silently empty.
    """

    problems: list[str]
    roots: list[str]
    workspaces: list[str]


class MarketCardOut(BaseModel):
    """One card in the market: what a vendor publishes, not anything of ours."""

    name: str
    description: str
    url: str
    source: str
    kind: str


class SkillMarketOut(BaseModel):
    skills: list[MarketCardOut]
    plugins: list[MarketCardOut]
    plugin_total: int
    mcp: list[MarketCardOut]
    #: A source that was down costs its own list; this says which.
    notes: list[str]


class InstallPlanItemOut(BaseModel):
    """What a fetch would write for one skill: its name and the files with it."""

    name: str
    description: str
    files: list[str]


class SkillFetchOut(BaseModel):
    """The look (``landed: false``) and the landing (``landed: true``) answer
    with the same body: the plan, and — once landed — the fresh index."""

    landed: bool
    plan: list[InstallPlanItemOut]
    skills: list[MachineSkillOut] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list)
    workspaces: list[str] = Field(default_factory=list)


async def _require_actor(resolver: ActorResolverDep) -> tuple[Actor, int]:
    """The signed-in person, and the id their own machines answer to."""
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise AuthenticationRequiredError(say("localDirSignIn"))
    return actor, actor.user_id


async def _require_project(
    resolver: ActorResolverDep, actor: Actor, project_id: uuid.UUID | None
) -> None:
    """A project the caller belongs to, when one was named.

    It decides whose session homes the scan reads, so naming a project that is
    not the caller's must not become a way to list that project's sessions.
    """
    if project_id is not None:
        await resolver.authorize_project(actor, project_id=project_id)


async def _require_owned_device(db: AsyncSession, user_id: int, device_id: str) -> Any:
    """Refuse unless the caller owns this machine — a stranger's is 「not yours」,
    reported as missing so the endpoint cannot enumerate other people's
    machines (``local_dirs.py`` takes the same stand). The device is returned so
    a caller can read the ``supply`` it decides the scan's scope by."""
    device = await sql_device_service(db).get_hosted_device(device_id)
    if device is None or device.owner_user_id != user_id:
        raise NotFoundError(say("deviceNotYours"))
    return device


def _scan_argv(device: Any, project_id: uuid.UUID | None) -> list[str]:
    """The scan program's argv: the project whose sessions to read, and whether
    the machine's own agent homes stay out.

    ``self_hosted`` is a machine the person enrolled themselves, so the
    ``~/.claude`` skills they wrote for their own agents are theirs — the scan
    reads none of the homes (``scan.py``), leaving only the roots the platform
    manages. A ``cloud`` machine was provisioned by the platform and has no such
    private homes to protect.
    """
    return [
        "python3",
        "-",
        str(project_id) if project_id else "",
        "1" if device.supply is Supply.self_hosted else "",
    ]


def _skill_view(row: Any) -> dict[str, Any]:
    return {
        "name": row.name,
        "description": row.description,
        "source_root": row.source_root,
        "digest": row.digest,
        "state": row.state,
        "adopted": row.confirmed_by is not None,
    }


@router.get("/my/devices/{device_id}/skills", **typed_response(MachineSkillListOut))
async def list_machine_skills(
    device_id: str, db: DbSession, resolver: ActorResolverDep
) -> dict[str, Any]:
    """The skills indexed on this machine, as they were last read."""
    _, user_id = await _require_actor(resolver)
    await _require_owned_device(db, user_id, device_id)
    rows = await MachineSkillService(db).list(device_id)
    return {"skills": [_skill_view(row) for row in rows]}


@router.get("/skills/market", **typed_response(SkillMarketOut))
async def read_skill_market(resolver: ActorResolverDep) -> dict[str, Any]:
    """What Anthropic and OpenAI publish, for the dialog's market.

    It names no machine: this is a public catalogue, not anything about a
    device, so signing in is the whole gate. The vendors' own listings are read
    live (``skill_market``), and a source that is down costs its own list rather
    than the market.
    """
    await _require_actor(resolver)
    return await skill_market.catalogue()


@router.post("/my/devices/{device_id}/skills/scan", **typed_response(SkillScanOut))
async def scan_machine_skills(
    device_id: str,
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Read the machine now, index what it has, and return the index.

    ``problems`` and ``roots`` come back beside the skills so a screen can say
    why the list is short — a directory that fails the spec, or a machine with
    none of the roots at all — rather than leaving an empty list unexplained.
    """
    actor, user_id = await _require_actor(resolver)
    device = await _require_owned_device(db, user_id, device_id)
    await _require_project(resolver, actor, project_id)
    return await _scan_now(db, device_id, device, project_id)


@router.post("/my/devices/{device_id}/skills/roots", **typed_response(SkillScanOut))
async def set_machine_skill_roots(
    device_id: str,
    body: SkillRootsRequest,
    db: DbSession,
    resolver: ActorResolverDep,
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Record the workspaces to scan, then read the machine again and return it.

    One call, because a screen only ever does the two together: the person has
    just named a checkout and wants to see what was in it. The paths are handed
    to the machine as argv and it writes its own list — so a path with a space
    needs no quoting, and a path that is not absolute is refused here rather
    than written to mean something else than it looked like.
    """
    actor, user_id = await _require_actor(resolver)
    device = await _require_owned_device(db, user_id, device_id)
    await _require_project(resolver, actor, project_id)
    roots: list[str] = []
    for value in body.roots:
        path = value.strip()
        if not path:
            continue
        if not path.startswith("/") or "\n" in path or "\r" in path:
            raise UnprocessableEntityError(say("skillRootNotAbsolute"))
        roots.append(path)
    written = await device_hub.exec(
        device_id,
        ["python3", "-", *roots],
        stdin=scan.set_program(),
        timeout=SCAN_TIMEOUT_S,
    )
    if written.get("exit") != 0 or written.get("truncated"):
        # i18n-exempt: 机器自己的 stderr，占位句只是它没说话时的占位
        raise DeviceCallError(
            str(written.get("stderr") or "writing skill roots failed")[-1500:]
        )
    return await _scan_now(db, device_id, device, project_id)


async def _scan_now(
    db: AsyncSession, device_id: str, device: Any, project_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Read the machine and index what it has — the body ``scan``, ``roots`` and
    ``web`` all end with.

    ``project_id`` rides along as argv[1]: the machine is told which project's
    session homes to look in, because it cannot guess and must not list another
    project's. argv[2] tells it whether to read its own agent homes at all —
    ``_scan_argv`` decides that from the machine's ``supply``.
    """
    result = await device_hub.exec(
        device_id,
        _scan_argv(device, project_id),
        stdin=scan.program(),
        timeout=SCAN_TIMEOUT_S,
    )
    try:
        found = scan.read(result)
    except scan.ScanFailed as failed:
        # 机器自己跑失败了：这是它的话，不是本服务器的 500（`core/errors.py`
        # 把 `DeviceCallError` 答成 502 并原样带上那段 stderr）。
        raise DeviceCallError(str(failed)) from failed
    rows = await MachineSkillService(db).record_scan(device_id, found["skills"])
    await db.commit()
    return {
        "skills": [_skill_view(row) for row in rows],
        "problems": found["problems"],
        "roots": found["roots"],
        "workspaces": found["workspaces"],
    }


@router.post("/my/devices/{device_id}/skills/web", **typed_response(SkillFetchOut))
async def fetch_machine_skill(
    device_id: str,
    body: WebSkillRequest,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict[str, Any]:
    """Fetch what an address points at, land it on the machine, then read it again.

    The address is read whole — the folder a GitHub link names, so its
    ``scripts/`` come with it — and checked against the agentskills.io rules
    *before* anything is written, because it lands on somebody's own disk. It is
    written under ``~/.cheese/web-skills/<name>``, which the scan reads like any
    other root, so from here it is adopted, un-adopted and carried exactly like
    a skill the machine already had.

    ``kind`` widens the address's meaning: a ``plugin`` is a folder bundling
    several skills, and each is landed under its own name.

    Two calls, not one: without ``confirm`` this is a look, not a write — read,
    judged, and answered with the plan. The person sees the skills and files
    about to reach their disk, and the second call (``confirm``) lands them.
    ``ponytail:`` the confirmed call reads the address again rather than holding
    the first read; a plugin with many skills spends the hour's GitHub requests
    twice per install. Ceiling: a slow confirmed install re-fetches.
    """
    actor, user_id = await _require_actor(resolver)
    device = await _require_owned_device(db, user_id, device_id)
    await _require_project(resolver, actor, body.project_id)
    address = normalize_skill_url(body.url)
    reads = (
        await read_github_many(address, keep_name=True)
        if body.kind == "plugin"
        else [await read_github(address, keep_name=True)]
    )
    # Every skill is judged before any is written: a plugin whose second skill is
    # refused must not leave its first one behind on somebody's disk.
    await _refuse_unlandable(db, device_id, reads)
    plan = _install_plan(reads)
    if not body.confirm:
        return {"landed": False, "plan": plan}
    for read in reads:
        files = _landed_files(read)
        written = await device_hub.exec(
            device_id,
            ["python3", "-"],
            stdin=scan.land_program(read.name, files),
            timeout=SCAN_TIMEOUT_S,
        )
        if written.get("exit") != 0 or written.get("truncated"):
            # i18n-exempt: 同上 —— 机器自己的 stderr，占位句只是它没说话时的那一句。
            raise DeviceCallError(
                str(written.get("stderr") or "landing the skill failed")[-1500:]
            )
    landed = await _scan_now(db, device_id, device, body.project_id)
    return {"landed": True, "plan": plan, **landed}


def _install_plan(reads: list[Any]) -> list[dict[str, Any]]:
    """What a fetch would write: each skill, and the files that come with it.

    Names only, no bytes: this is what a person reads before saying yes, and
    the file list is what makes 「it also brings three scripts」 visible.
    """
    return [
        {
            "name": read.name,
            "description": read.description,
            "files": sorted(_landed_files(read)),
        }
        for read in reads
    ]


def _landed_files(read: Any) -> dict[str, str]:
    """The bytes written under a skill's folder: its SKILL.md and its files."""
    return {"SKILL.md": read.raw, **read.files}


async def _refuse_unlandable(
    db: AsyncSession, device_id: str, reads: list[Any]
) -> None:
    """Raise unless every read may be landed — spec, size, and name are free.

    A name this machine already has, that did not come from here, is somebody
    else's skill: landing over it would silently re-point their adopted row at a
    different body. Refuse instead, and let the person rename or unadopt.
    """
    service = MachineSkillService(db)
    for read in reads:
        refusal = spec.check(read.name, read.description)
        if refusal:
            raise UnprocessableEntityError(say(refusal))
        files = _landed_files(read)
        if sum(len(text.encode("utf-8")) for text in files.values()) > MAX_TOTAL_BYTES:
            raise UnprocessableEntityError(say("skillWebTooLarge"))
        taken = await service.find(device_id, read.name)
        if taken is not None and scan.WEB_SKILLS_MARK not in taken.source_root:
            raise UnprocessableEntityError(say("skillWebNameTaken"))


@router.post(
    "/my/devices/{device_id}/skills/{name}/adopt", **typed_response(MachineSkillOut)
)
async def set_machine_skill_adoption(
    device_id: str,
    name: str,
    body: AdoptionRequest,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict[str, Any]:
    """A person's decision about one skill on their own machine.

    Adopting lets it be offered when a teammate's skills are picked; un-adopting
    takes that back, and a teammate that had it stops carrying it (``carried_bodies``
    only reads a machine skill's body back while its row is confirmed).
    """
    user_id = (await _require_actor(resolver))[1]
    await _require_owned_device(db, user_id, device_id)
    row = await MachineSkillService(db).set_adoption(
        device_id, name, adopted=body.adopted, by=user_id
    )
    await db.commit()
    return _skill_view(row)
