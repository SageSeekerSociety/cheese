"""The project's AI teammate hands the project's work in to its 题目, for a
member, on that member's word.

Two steps, and the consent between them is held here rather than by what
the teammate is told:

- `POST /topics/{conversation}/hand-in/prepare`: the teammate names, for
  each submission item of one stage, a file of the project (the repository's
  default branch as committed, or the library) or the text. Everything a
  hand-in on the 题目 page is checked for is checked now, what each item was
  read at is kept (`prepared_hand_ins`), and nothing is handed in. It is
  prepared for the member whose message started this turn.
- `POST /topics/{conversation}/hand-in/confirm`: in a turn that member started
  with a message sent after the preparation, the platform hands it in as them,
  through the same path the page takes (`app.api.task_hand_in`), with the
  bytes as they were read, and records in the conversation that the teammate
  handed it in for them. Whether the message says yes is the teammate's
  judgement; that it is the member's own, and later, is checked here — the
  shape of the main line adopting on a reviewer's word
  (`topics_tasks.accept_coordinated_task`).

The 题目, its submission form and its grading do not change: the teacher sees
an ordinary submission whose submitter is the member.
"""

import io
import posixpath
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.task_claims import project_of_claim
from app.api.task_deps import get_task_submission_service
from app.api.task_hand_in import hand_in_version, refuse_closed, refuse_used_up
from app.api.write_access import Can, InChannel
from app.auth import can
from app.auth.task_roles import ClaimPermission
from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import listing, say
from app.core.storage import get_storage_backend
from app.domain.agent.announce import announce, open_turn_of, word_that_started
from app.domain.agent.room.thread_context import member
from app.domain.agent.turn.intake.rooms import room_roster
from app.domain.attachment.services import AttachmentService
from app.domain.identity.handles import names_a_person
from app.domain.library import records as library_records
from app.domain.library.shelf import Shelf
from app.domain.notice_meta import (
    EVENT_TEAMMATE_HANDED_IN,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.project.services import ProjectService
from app.domain.project_permissions import Permission
from app.domain.repository.forge_files import ProjectFiles, clean_path
from app.domain.task import code_version
from app.domain.task import prepared_hand_ins as prepared
from app.domain.task.claims import project_claim, task_row
from app.domain.task.services import TaskSubmissionService
from app.domain.task.stages import list_stages
from app.domain.user.services import user_by_handle

router = APIRouter(prefix="/topics", tags=["topics"])

#: Where a file named under this prefix is read from: the project's library.
#: Any other path is a path in the repository.
LIBRARY = "library/"


class HandInItemIn(BaseModel):
    #: The item's number in the 题目's submission form, as its brief lists them.
    item: int = Field(ge=1)
    #: For a file item: a path in the repository, or ``library/<name>``.
    file: str | None = Field(default=None, max_length=1024)
    #: For a text item: the text.
    text: str | None = Field(default=None, max_length=100_000)


class PrepareIn(BaseModel):
    items: list[HandInItemIn] = Field(min_length=1, max_length=100)


class ConfirmIn(BaseModel):
    #: Which stage's preparation, by its number in the 题目's stages; left out,
    #: the latest one prepared here.
    stage: int | None = Field(default=None, ge=1)


async def _here(db, at: InChannel):
    """The project of this conversation, the 题目 it was made from, and the
    claim it was made for."""
    project = await ProjectService(db).get_or_404(at.place.project_id)
    task = (
        await task_row(db, project.external_task_id)
        if project.external_task_id is not None
        else None
    )
    if task is None or task.deleted_at is not None:
        raise ConflictError(say("handInNoTask"))
    claim = await project_claim(db, task=task, team_id=project.team_id)
    if claim is None:
        raise ConflictError(say("handInNoClaim", task=task.name))
    return project, task, claim


async def _may_hand_in(db, claim, user_id: int) -> bool:
    return await can.policy_of(ClaimPermission.hand_in).may(
        db, claim, user_id, ClaimPermission.hand_in
    )


async def _revision(db, project, *, reads_repository: bool) -> str:
    """The repository version a hand-in is read at and judged on: what the
    submission records (`code_version`), or — for a project whose version is
    not recorded, such as one on GitHub — the default branch's head when a
    file is read from it; "" when there is neither."""
    pending = await code_version.for_project(db, project.id)
    if pending is not None:
        return pending.sha
    if reads_repository:
        return await ProjectFiles(db, project.id, None).revision()
    return ""


def _library_shelf(project) -> Shelf:
    # The project's own files: the 题目's materials (`题目/`) are not work to
    # hand back to it.
    return Shelf.of_project(project.id, with_task=False)


async def _library_current(db, project, name: str) -> dict:
    try:
        versions = await library_records.versions(db, _library_shelf(project), name)
    except NotFoundError:
        raise NotFoundError(say("handInLibraryFileNotFound", name=name)) from None
    current = next((v for v in versions if v["current"]), None)
    if current is None:
        raise NotFoundError(say("handInLibraryFileNotFound", name=name))
    return current


def _refuse_too_large(path: str, size: int) -> None:
    limit = settings.attachment_max_bytes
    if size > limit:
        mb = 1024 * 1024
        raise UnprocessableEntityError(
            say(
                "handInFileTooLarge",
                path=path,
                mb=round(size / mb, 1),
                max=limit // mb,
            )
        )


def _wanted(form, given: list[HandInItemIn], task) -> tuple[int, list, dict]:
    """The stage the named items belong to, that stage's items in the form's
    order, and what was given for each, by number."""
    by_number = {item.number: item for item in form}
    named: dict[int, HandInItemIn] = {}
    for item in given:
        if item.item not in by_number:
            raise ValidationError(
                say("handInItemUnknown", task=task.name, item=item.item)
            )
        if item.item in named:
            raise ValidationError(say("handInItemTwice", item=item.item))
        named[item.item] = item
    stages = {by_number[n].stage for n in named}
    if len(stages) > 1:
        raise ValidationError(say("handInItemsAcrossStages"))
    [stage] = stages
    return stage, [item for item in form if item.stage == stage], named


@router.post(
    "/{topic_id}/hand-in/prepare",
    summary="Prepare A Hand-In For A Member",
)
async def prepare_hand_in(
    body: PrepareIn,
    at: Annotated[InChannel, Can(Permission.hand_in_for_members, at="topic_id")],
    db: DbSession,
    submissions: Annotated[TaskSubmissionService, Depends(get_task_submission_service)],
) -> dict:
    """Prepare, for the member whose message started this turn, what goes
    into each submission item of one stage of the project's 题目. Checked as
    a hand-in on the 题目 page would be; nothing is handed in. A newer
    preparation for the same stage replaces this one."""
    project, task, claim = await _here(db, at)
    turn = await open_turn_of(
        db, at.place.project_id, at.place.conversation_id, at.actor.handle
    )
    said = await word_that_started(db, turn) if turn is not None else None
    person = (
        await user_by_handle(db, turn.author)
        if said is not None and turn is not None and names_a_person(turn.author)
        else None
    )
    if person is None or not await _may_hand_in(db, claim, person.id):
        raise ForbiddenError(say("handInAskedByMember", task=task.name))

    form = await prepared.form_items(db, task.id)
    stage, stage_items, named = _wanted(form, body.items, task)
    await refuse_closed(db, task=task, membership=claim, stage=stage)
    await refuse_used_up(submissions, task=task, membership=claim, stage=stage)

    missing, files = [], {}
    for item in stage_items:
        given = named.get(item.number)
        file = ((given.file if given else None) or "").strip()
        text = (given.text if given else None) or ""
        if not file and not text.strip():
            missing.append(item.name)
        elif item.kind == "TEXT" and file:
            raise ValidationError(
                say("handInWantsText", item=item.number, name=item.name)
            )
        elif item.kind == "FILE" and not file:
            raise ValidationError(
                say("handInWantsFile", item=item.number, name=item.name)
            )
        elif item.kind == "FILE":
            files[item.number] = file
    if missing:
        raise ValidationError(
            say("handInItemMissing", items=listing(missing, quoted=True))
        )

    reads_repository = any(not f.startswith(LIBRARY) for f in files.values())
    revision = await _revision(db, project, reads_repository=reads_repository)
    committed = (
        {
            entry["path"]: entry
            for entry in await ProjectFiles(db, project.id, None).committed_entries(
                revision
            )
        }
        if reads_repository
        else {}
    )

    items = []
    for item in stage_items:
        row: dict = {"number": item.number, "name": item.name, "kind": item.kind}
        if item.kind == "TEXT":
            row["text"] = named[item.number].text
        elif files[item.number].startswith(LIBRARY):
            name = files[item.number].removeprefix(LIBRARY).strip("/")
            current = await _library_current(db, project, name)
            _refuse_too_large(files[item.number], current["bytes"])
            row |= {
                "source": "library",
                "path": name,
                "bytes": current["bytes"],
                "version": current["id"],
                "libraryVersion": current["version"],
            }
        else:
            path = clean_path(posixpath.normpath(files[item.number]))
            entry = committed.get(path)
            if entry is None or entry["kind"] != "blob":
                raise NotFoundError(say("handInRepoFileNotFound", path=path))
            _refuse_too_large(path, entry["bytes"])
            row |= {
                "source": "repository",
                "path": path,
                "bytes": entry["bytes"],
                "version": entry["oid"],
            }
        items.append(row)

    kept = await prepared.replace(
        db,
        project_id=project.id,
        membership_id=claim.id,
        stage=stage,
        for_user_id=person.id,
        for_handle=person.username,
        prepared_by=at.actor.handle,
        conversation_id=at.place.conversation_id,
        revision=revision,
        items=items,
    )
    stages = await list_stages(db, task.id)
    await db.commit()
    return ok(
        {
            "task": task.name,
            "stage": stages[stage].name if stage < len(stages) else None,
            "stageNumber": stage + 1 if stages else None,
            "for": kept.for_handle,
            "revision": revision,
            "items": items,
        }
    )


@router.post(
    "/{topic_id}/hand-in/confirm",
    summary="Hand In What Was Prepared, On The Member's Word",
)
async def confirm_hand_in(
    body: ConfirmIn,
    at: Annotated[InChannel, Can(Permission.hand_in_for_members, at="topic_id")],
    db: DbSession,
    submissions: Annotated[TaskSubmissionService, Depends(get_task_submission_service)],
) -> dict:
    """Hand in what was prepared in this conversation, as the member it was
    prepared for, in a turn that member started with a message sent after it
    was prepared. Refused when the project's files changed since: the member
    agreed to what was shown."""
    project, task, claim = await _here(db, at)
    kept = await prepared.waiting(
        db,
        membership_id=claim.id,
        conversation_id=at.place.conversation_id,
        stage=body.stage - 1 if body.stage is not None else None,
    )
    if kept is None:
        raise ConflictError(say("handInNothingPrepared"))
    roster = await room_roster(db, at.place.project_id, at.place.room)
    person = member(kept.for_handle, {m["handle"]: m["name"] for m in roster})

    turn = await open_turn_of(
        db, at.place.project_id, at.place.conversation_id, at.actor.handle
    )
    said = await word_that_started(db, turn) if turn is not None else None
    if (
        turn is None
        or said is None
        or turn.author != kept.for_handle
        or not await _may_hand_in(db, claim, kept.for_user_id)
    ):
        raise ForbiddenError(say("handInMemberWordOnly", person=person))
    if said.created_at <= kept.created_at:
        raise ConflictError(say("handInWordBeforePrepare", person=person))

    await refuse_closed(db, task=task, membership=claim, stage=kept.stage)
    await refuse_used_up(submissions, task=task, membership=claim, stage=kept.stage)
    owner = await project_of_claim(
        db, task=task, membership=claim, user_id=kept.for_user_id
    )
    if owner is None or owner.id != project.id:
        raise ConflictError(say("handInNoClaim", task=task.name))

    changed = say("handInFilesChanged", person=person)
    reads_repository = any(i.get("source") == "repository" for i in kept.items)
    if await _revision(db, project, reads_repository=reads_repository) != kept.revision:
        raise ConflictError(changed)
    for item in kept.items:
        if item.get("source") == "library":
            current = await _library_current(db, project, item["path"])
            if current["id"] != item["version"]:
                raise ConflictError(changed)

    attachments = AttachmentService.from_session(
        session=db, storage=get_storage_backend()
    )
    files = ProjectFiles(db, project.id, None)
    contents: list[dict] = []
    for item in kept.items:
        if item["kind"] == "TEXT":
            contents.append({"text": item["text"]})
            continue
        if item["source"] == "library":
            data = await library_records.version_bytes(
                db, _library_shelf(project), item["path"], uuid.UUID(item["version"])
            )
        else:
            data = await files.raw_at(item["path"], kept.revision)
            if data is None:
                raise ConflictError(changed)
        stored = await attachments.upload(
            file=io.BytesIO(data),
            filename=posixpath.basename(item["path"]),
            uploader_id=kept.for_user_id,
        )
        contents.append({"attachmentId": stored.id})

    submission = await hand_in_version(
        db,
        task=task,
        membership=claim,
        stage=kept.stage,
        submitter_id=kept.for_user_id,
        contents=contents,
        submissions=submissions,
        code_sha=kept.revision or None,
        moved=changed,
    )
    prepared.mark_handed_in(kept, int(submission["id"]))
    conversation = at.place.conversation_id
    await announce(
        db,
        place_id=at.place.room_id,
        task_id=None if conversation == at.place.room_id else conversation,
        content=say(
            "teammateHandedIn",
            who=say(
                "actedByTeammate",
                agent=f"<@{at.actor.handle}>",
                person=f"<@{kept.for_handle}>",
            ),
            task=task.name,
            version=submission["version"],
        ),
        meta=notice(EVENT_TEAMMATE_HANDED_IN, severity=SEVERITY_INFO, who=WHO_PLATFORM),
    )
    await db.commit()
    return ok(
        {
            "task": task.name,
            "for": kept.for_handle,
            "version": submission["version"],
            "submission": int(submission["id"]),
        }
    )
