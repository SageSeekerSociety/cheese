"""cheese_skill(action=search) / cheese_skill(action=read) for AI teammates: find an installed skill, then
read it whole (OpenClaw's Installed-skill discovery, over the platform).

``installed`` is what a session in this room can already load with nobody asked
— the three sources ``carried_bodies`` reads from:

* the platform's shipped skills (``agent/skills.py``);
* this project's **confirmed** skills (``project_skill``; a teammate's draft is
  not shipped yet, so it is not installed);
* the skills indexed on the machine this room runs on (``machine_skill``) —
  never another person's machine (``room_device`` names only this room's).
  A row on the index is enough; the old adopt/un-adopt gate is gone.

Search answers names and descriptions only — an index, not the library — so a
big skill set costs no prompt tokens. ``cheese_skill(action=read)`` is a separate call and is
the only place a body comes back, and it grants reading alone: neither
installing nor running anything.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.domain import skill_library as native_skills
from app.domain.agent.carried_bodies import carried_bodies, room_device
from app.domain.machine_skill.service import MachineSkillService
from app.domain.project_skill.service import ProjectSkillService, render_skill
from app.domain.room_task.place import place_or_404

router = APIRouter(
    prefix="/agent/skills", tags=["agent-skills"], dependencies=[ROUTE_DECIDES]
)

#: Names are short labels; the room the caller names is what scopes the set.
_SOURCE_PLATFORM = "平台发货"
_SOURCE_PROJECT = "项目技能"
_SOURCE_MACHINE = "这台机器"

#: The fields a project skill's shipped SKILL.md is rendered from
#: (``ProjectSkillService`` ``FIELDS``).
_CONTENT_FIELDS = ("title", "description", "body", "files")


class SkillsQuery(BaseModel):
    #: The room the caller speaks from — it decides the project and the machine.
    topic: uuid.UUID
    query: str = Field(min_length=1, max_length=300)


class SkillsName(BaseModel):
    topic: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


async def _room(db: AsyncSession, topic: uuid.UUID, resolver) -> object:
    """Authorize the caller for the room it names and hand back its place.

    The project and the machine both come from the room, so a caller reaches no
    other project's and no other person's skill — the same gate
    ``docs_site._agent_scope`` puts in front of the docs tools.
    """
    place = await place_or_404(db, topic)
    who = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        who, project_id=place.project_id, topic_id=place.room_id
    )
    return place


def _project_markdown(row) -> str:
    """The SKILL.md a session receives for one confirmed project skill."""
    content = {key: getattr(row, key) for key in _CONTENT_FIELDS}
    return render_skill(
        row.name,
        content,
        skill_id=row.id,
        revision=row.shipped_revision,
        confirmed_by=row.confirmed_by or "",
    )


async def _installed(db: AsyncSession, place) -> dict[str, dict]:
    """Every installed skill this room can load, by name.

    First source wins on a name collision, and the order is the order a session
    itself resolves them in (platform, project, machine)."""
    found: dict[str, dict] = {}

    def add(name: str, description: str, source: str, markdown: str) -> None:
        found.setdefault(
            name,
            {
                "name": name,
                "description": description,
                "source": source,
                "markdown": markdown,
            },
        )

    for name in native_skills.shipped_skill_names():
        doc = native_skills.native_skill_doc(name)
        root = native_skills.native_skill_dir(name)
        if doc is not None and root is not None:
            add(name, doc[0], _SOURCE_PLATFORM, (root / "SKILL.md").read_text("utf-8"))
    for row in await ProjectSkillService(db).list(place.project_id):
        # A draft is a teammate's proposal a person has not saved: not shipped,
        # so not installed.
        if row.state == "active":
            add(row.name, row.description, _SOURCE_PROJECT, _project_markdown(row))
    device_id = room_device(place.room)
    if device_id is not None:
        for row in await MachineSkillService(db).list(device_id):
            # The body is read back the same way as a turn, so an offline
            # machine simply reads empty.
            body = await carried_bodies(db, place.room, [row.name])
            add(row.name, row.description, _SOURCE_MACHINE, body)
    return found


@router.post("/search", include_in_schema=False)
async def search_installed_skills(
    body: SkillsQuery, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """cheese_skill(action=search): the installed skills matching a query, names only."""
    place = await _room(db, body.topic, resolver)
    terms = [word for word in body.query.split() if word]
    skills = []
    for skill in (await _installed(db, place)).values():
        haystack = " ".join(
            (skill["name"], skill["description"], skill["markdown"])
        ).lower()
        if all(term.lower() in haystack for term in terms):
            skills.append(
                {
                    "name": skill["name"],
                    "description": skill["description"],
                    "source": skill["source"],
                }
            )
    return ok({"skills": sorted(skills, key=lambda s: s["name"])})


@router.post("/read", include_in_schema=False)
async def read_installed_skill(
    body: SkillsName, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """cheese_skill(action=read): one installed skill's whole SKILL.md, or ``found: false``."""
    place = await _room(db, body.topic, resolver)
    name = body.name.strip()
    skill = (await _installed(db, place)).get(name)
    if skill is None:
        return ok({"found": False, "name": name})
    return ok(
        {
            "found": True,
            "name": skill["name"],
            "source": skill["source"],
            "markdown": skill["markdown"],
        }
    )
