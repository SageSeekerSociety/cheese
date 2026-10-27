"""Drafts of 题目 (``app.domain.task.drafts``): the publish and edit pages
save into one as the person writes, and their 芝士 writes into it with its
tools.

Two sets of routes over the same drafts:

* ``/task-drafts`` and ``/task-drafts/{id}``: the page's. It lists, opens,
  saves and publishes the person's drafts, in the form's own shape.
* ``/task-drafts/agent/*``: 芝士's tools (``sandbox/cheese``), reached with the
  credential a question is answered with, and so acting as the person who
  asked. They speak 芝士's words (``drafts.AGENT_FIELDS``),
  answer in text it can read, and tell the page what changed: each write is
  kept (``assistant.changes``, to be undone) and goes out on the question's
  own stream as a ``change``, which the page applies to the form as it is
  shown.

A draft is the person's own. Somebody else's reads as missing.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.auth import ActorResolverDep
from app.api.deps import get_consumptions
from app.api.response import ok, typed_response
from app.api.routes.tasks._common import CreateTaskRequest, PatchTaskRequest
from app.api.routes.tasks._writes import create_task_from, update_task
from app.api.space_deps import get_space_service
from app.api.task_deps import (
    _attachment_service,
    get_task_service,
)
from app.api.task_serialization import (
    _enrich_task_models,
    _enrich_task_topics,
    _task_requirements,
    _task_to_api_model,
)
from app.api.write_access import ROUTE_DECIDES
from app.auth import task_roles as task_seats
from app.core import model_roles
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sentences import NoticeText, say
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.agent import gateway_catalog
from app.domain.agent.personal import service as assistant
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.assistant import changes
from app.domain.attachment.reading import read_document
from app.domain.library import records as library_records
from app.domain.space import permissions as space_seats
from app.domain.space.material_service import SpaceMaterialService
from app.domain.space.services import SpaceService
from app.domain.task import drafts, package
from app.domain.task.inputs import APPROVE_APPROVED, APPROVE_DISAPPROVED
from app.domain.task.services import TaskService, ensure_task_readable
from app.domain.task.stages import list_stages, stage_lines, stages_to_api
from app.domain.task.submission_state import stage_change_effect
from app.domain.user.services import timezones_by_ids

router = APIRouter(prefix="/task-drafts", tags=["Tasks"])


class TaskDraftSummaryOut(BaseModel):
    """A draft as the draft list shows it."""

    id: int
    spaceId: int
    #: The published 题目 these are changes to; None for a new one.
    taskId: int | None
    taskName: str | None
    name: str
    #: The file it came from; empty when it was started by hand.
    source: str
    #: The 芝士 conversation working on it.
    conversationId: str | None
    version: int
    updatedAt: str
    #: What still has to be written before it can be published, in 芝士's
    #: field words (name, description, form, difficulty).
    missing: list[str]


class TaskDraftOut(TaskDraftSummaryOut):
    """A draft, with its form (the shape ``POST /tasks`` takes)."""

    fields: dict


class TaskDraftListOut(BaseModel):
    drafts: list[TaskDraftSummaryOut]


class TaskDraftSavedOut(BaseModel):
    version: int
    updatedAt: str


class TaskDraftPublishedOut(BaseModel):
    """What ``POST /tasks`` or ``PATCH /tasks/{id}`` answers, and the draft
    that is gone now."""

    task: dict
    draftId: int


class TaskDraftGoneOut(BaseModel):
    """Nothing: the draft is gone."""


Spaces = Annotated[SpaceService, Depends(get_space_service)]
Tasks = Annotated[TaskService, Depends(get_task_service)]
Questions = Annotated[Consumptions, Depends(get_consumptions)]

#: How many 题目 a search answers with.
SEARCH_LIMIT = 12
#: A 题目's review, as the API names it.
_APPROVALS = {APPROVE_APPROVED: "APPROVED", APPROVE_DISAPPROVED: "DISAPPROVED"}


async def _person(resolver: ActorResolverDep) -> int:
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise AuthenticationRequiredError(say("invalidToken"))
    return actor.user_id


def _summary(row, task_names: dict[int, str], items: dict[int, int]) -> dict:
    fields = row.fields or {}
    return {
        "id": row.id,
        "spaceId": row.space_id,
        "taskId": row.task_id,
        "taskName": task_names.get(row.task_id) if row.task_id else None,
        "name": fields.get("name") or "",
        "source": row.source,
        "conversationId": str(row.conversation_id) if row.conversation_id else None,
        "version": row.version,
        "updatedAt": row.updated_at.isoformat(),
        "missing": drafts.missing(row, items.get(row.task_id or 0, 0)),
    }


def _full(row, task_names: dict[int, str], items: dict[int, int]) -> dict:
    return {**_summary(row, task_names, items), "fields": row.fields or {}}


async def _task_names(db, rows: list) -> dict[int, str]:
    return await drafts.task_names(db, rows)


async def _zone(db, user_id: int) -> str | None:
    return (await timezones_by_ids(db, [user_id])).get(user_id)


async def _publishable_space(db, space_id: int, user_id: int) -> None:
    if not await space_seats.SPACE.allows(
        db, space_id, user_id, space_seats.Permission.publish
    ):
        raise NotFoundError.for_resource("space", space_id)


async def _editable_task(db, task_id: int, user_id: int):
    task = await TaskService.of(db).get_task(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    if not await task_seats.teaches(db, task, user_id):
        raise ForbiddenError(say("draftTaskNotYours"))
    return task


async def _task_as_form(db, task, *, private: bool) -> dict:
    """The published 题目 as its edit form starts out: every field the form
    saves back, so a change draft saved with one field changed leaves the rest
    as they are — its stages, submission items, rubric and tags included.
    ``private``: the reader may teach the 题目 (``task_roles.teaches``), so its
    private guidance comes with it; anyone else gets the form without it."""
    service = TaskService.of(db)
    model = _task_to_api_model(task, include_private_teaching=private)
    model.update(await _task_requirements(service, task.id))
    model = (await _enrich_task_models(db, [model], space_id=task.space_id))[0]
    await _enrich_task_topics(db, [model])
    return drafts.task_form(model)


async def _edit_draft(db, task, user_id: int, conversation_id=None):
    """The person's unsaved changes to ``task``: the ones they have, or new
    ones starting from the 题目 as it is."""
    found = await drafts.for_task(db, user_id, task.id)
    if found is not None:
        return found
    return await drafts.start(
        db,
        user_id=user_id,
        space_id=task.space_id,
        task_id=task.id,
        # Only those who may teach it get here (``_editable_task``).
        fields=await _task_as_form(db, task, private=True),
        conversation_id=conversation_id,
    )


# --- the page's ----------------------------------------------------------------


@router.get("", **typed_response(TaskDraftListOut))
async def list_drafts(
    resolver: ActorResolverDep,
    space: Annotated[int, Query(ge=1)],
    db=Depends(get_db),
) -> dict:
    """The person's drafts in this space, latest first."""
    user_id = await _person(resolver)
    rows = await drafts.list_mine(db, user_id, space)
    names = await _task_names(db, rows)
    items = await drafts.published_items(db, rows)
    return ok({"drafts": [_summary(r, names, items) for r in rows]})


class StartIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    space: int | None = None
    #: Changes to this published 题目: its draft is opened, or started from it.
    task: int | None = None
    fields: dict = Field(default_factory=dict)
    source: str = ""


@router.post(
    "", status_code=201, dependencies=[ROUTE_DECIDES], **typed_response(TaskDraftOut)
)
async def start_draft(
    body: StartIn, resolver: ActorResolverDep, db=Depends(get_db)
) -> dict:
    """A new draft in a space, or the person's changes to a published 题目
    (the ones they already have, or new ones starting from the 题目 as it is)."""
    user_id = await _person(resolver)
    if body.task is not None:
        task = await _editable_task(db, body.task, user_id)
        row = await _edit_draft(db, task, user_id)
        names = {task.id: task.name}
    else:
        if body.space is None:
            raise BadRequestError(say("draftNeedsSpace"))
        await _publishable_space(db, body.space, user_id)
        row = await drafts.start(
            db,
            user_id=user_id,
            space_id=body.space,
            fields=body.fields,
            source=body.source,
        )
        names = {}
    await db.commit()
    return ok(_full(row, names, await drafts.published_items(db, [row])))


class TaskChangesOut(BaseModel):
    """What the edit page of a published 题目 opens on: the person's unsaved
    changes if they have some, and the form it starts from."""

    draft: TaskDraftOut | None
    #: The changes' form, or the 题目 as it is when there are none.
    form: dict


@router.get("/changes", **typed_response(TaskChangesOut))
async def read_changes(
    resolver: ActorResolverDep,
    task: Annotated[int, Query(ge=1)],
    db=Depends(get_db),
) -> dict:
    """The person's changes to a published 题目, without starting any: opening
    the edit page is not changing the 题目, so the draft is started
    (``POST /task-drafts`` with ``task``) by the first change or the first
    question to 芝士."""
    user_id = await _person(resolver)
    found = await _editable_task(db, task, user_id)
    row = await drafts.for_task(db, user_id, found.id)
    if row is None:
        form = await _task_as_form(db, found, private=True)
        return ok({"draft": None, "form": form})
    shown = _full(row, {found.id: found.name}, await drafts.published_items(db, [row]))
    return ok({"draft": shown, "form": shown["fields"]})


class StarterUploadedOut(BaseModel):
    """The starter code, kept as the person's own attachment: its id goes into
    the draft as ``starterAttachmentId``."""

    id: int


@router.post(
    "/starter",
    status_code=201,
    dependencies=[ROUTE_DECIDES],
    **typed_response(StarterUploadedOut),
)
async def upload_starter(
    resolver: ActorResolverDep,
    file: UploadFile = File(...),
    db=Depends(get_db),
) -> dict:
    """The publish page sends the starter code the moment it is chosen. It is
    checked first, with the check the 题目 runs when the archive is put in: a
    bad one is refused with the reason and nothing is stored, rather than
    finding out after the 题目 is published."""
    user_id = await _person(resolver)
    attachment_id = await package.upload_starter(
        _attachment_service(db),
        by=user_id,
        file=file.file,
        filename=file.filename or "starter.zip",
    )
    # Commit before answering: the id is saved into the draft on the page's
    # next request, which must find the row.
    await db.commit()
    return ok({"id": attachment_id})


@router.get("/{draftId:int}", **typed_response(TaskDraftOut))
async def read_draft(
    draft_id: Annotated[int, Path(ge=1, alias="draftId")],
    resolver: ActorResolverDep,
    db=Depends(get_db),
) -> dict:
    user_id = await _person(resolver)
    row = await drafts.mine(db, user_id, draft_id)
    shown = _full(
        row,
        await _task_names(db, [row]),
        await drafts.published_items(db, [row]),
    )
    if not await drafts.private_shown(db, row):
        shown["fields"] = drafts.without_private(shown["fields"])
    return ok(shown)


class SaveIn(BaseModel):
    fields: dict


@router.patch(
    "/{draftId:int}", dependencies=[ROUTE_DECIDES], **typed_response(TaskDraftSavedOut)
)
async def save_draft(
    draft_id: Annotated[int, Path(ge=1, alias="draftId")],
    body: SaveIn,
    resolver: ActorResolverDep,
    db=Depends(get_db),
) -> dict:
    """The person changed these fields on the page."""
    user_id = await _person(resolver)
    row = await drafts.mine(db, user_id, draft_id)
    # The publish copies the starter only from the person's own upload; a draft
    # holds no other, so a foreign one is refused here, not at publish.
    starter = body.fields.get("starterAttachmentId")
    if starter is not None:
        if not isinstance(starter, int) or isinstance(starter, bool):
            raise BadRequestError(say("draftFileNotYours"))
        await package.refuse_unless_own_uploads(
            _attachment_service(db), [starter], user_id
        )
    drafts.person_writes(row, body.fields)
    await db.commit()
    return ok({"version": row.version, "updatedAt": row.updated_at.isoformat()})


@router.delete(
    "/{draftId:int}", dependencies=[ROUTE_DECIDES], **typed_response(TaskDraftGoneOut)
)
async def delete_draft(
    draft_id: Annotated[int, Path(ge=1, alias="draftId")],
    resolver: ActorResolverDep,
    db=Depends(get_db),
) -> dict:
    user_id = await _person(resolver)
    row = await drafts.mine(db, user_id, draft_id)
    await db.delete(row)
    await db.commit()
    return ok({})


@router.post(
    "/{draftId:int}/publish",
    dependencies=[ROUTE_DECIDES],
    **typed_response(TaskDraftPublishedOut),
)
async def publish_draft(
    draft_id: Annotated[int, Path(ge=1, alias="draftId")],
    resolver: ActorResolverDep,
    service: Tasks,
    db=Depends(get_db),
) -> dict:
    """Publish the draft: a new 题目, or the changes saved onto the published
    one. The draft is gone after."""
    user_id = await _person(resolver)
    row = await drafts.mine(db, user_id, draft_id)
    # The same loss 芝士's publish refuses: every submission item the
    # published 题目 has, gone.
    published = (await drafts.published_items(db, [row])).get(row.task_id or 0, 0)
    if "submission" in drafts.missing(row, published):
        raise BadRequestError(say("draftKeepsASubmissionItem"))
    return ok(await _publish(db, service, row, user_id))


async def _publish(db, service: TaskService, row, user_id: int) -> dict:
    form = drafts.published_form(row)
    task_id = row.task_id
    creating: CreateTaskRequest | None = None
    changing: PatchTaskRequest | None = None
    try:
        if task_id is None:
            creating = CreateTaskRequest.model_validate(
                {
                    "resubmittable": True,
                    "editable": True,
                    **form,
                    "space": row.space_id,
                }
            )
        else:
            changing = PatchTaskRequest.model_validate(
                {**form, "hasDeadline": form.get("deadline") is not None}
            )
    except ValidationError as exc:
        missing = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")})
        raise BadRequestError(
            say("draftIncomplete", fields=", ".join(missing))
        ) from exc
    await db.delete(row)
    await db.flush()
    if creating is not None:
        answer = await create_task_from(creating, db=db, user_id=user_id)
    else:
        assert task_id is not None and changing is not None
        # The files 芝士 added to the changes (``attachmentIds``, and the
        # starter code in ``starterAttachmentId``) go into the 题目's library
        # with them, as one version.
        answer = await update_task(
            task_id, changing, db=db, service=service, user_id=user_id
        )
    return {**answer, "draftId": row.id}


# --- 芝士's ----------------------------------------------------------------------


async def _space_terms(db, spaces: SpaceService, space_id: int, user_id: int):
    return await drafts.terms(
        db,
        spaces,
        space_id,
        await _zone(db, user_id),
        user_id=user_id,
        materials=await _library(db, space_id, user_id),
    )


async def _draft_text(db, spaces: SpaceService, row, user_id: int) -> str:
    return await drafts.context(
        db,
        spaces,
        row,
        await _zone(db, user_id),
        materials=await _library(db, row.space_id, row.user_id),
    )


async def _library(db, space_id: int, user_id: int) -> dict[str, int]:
    """The space library's files this person sees, by name: what 芝士 may
    point the 题目's AI teammates at (the page's picker lists the same)."""
    try:
        listed = await SpaceMaterialService(
            session=db, storage=get_storage_backend()
        ).list_for_space(space_id=space_id, user_id=user_id)
    except NotFoundError:
        # Not one of the space's people: its library is not theirs to see,
        # so there is nothing in it they could point the teammates at.
        return {}
    return {str(m["name"]): int(m["id"]) for m in listed["materials"]}


class SearchIn(BaseModel):
    space: int
    query: str = ""


@router.post("/agent/search", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_search(
    body: SearchIn, resolver: ActorResolverDep, db=Depends(get_db)
) -> dict:
    """`cheese_find` in a space (`where=spaces/<id>`): the space's 题目 whose name or intro has every word
    of the query, and the person's drafts there."""
    user_id = await _person(resolver)
    await _publishable_space(db, body.space, user_id)
    words = [w.lower() for w in body.query.split() if w]
    found = await TaskService.of(db).search_in_space(
        body.space, words, viewer_id=user_id, limit=SEARCH_LIMIT
    )
    mine = [
        r
        for r in await drafts.list_mine(db, user_id, body.space)
        if all(w in str((r.fields or {}).get("name") or "").lower() for w in words)
    ]
    return ok(
        {
            "tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "intro": t.intro,
                    "approved": _APPROVALS.get(t.approved, "NONE"),
                    "deadline": t.deadline.isoformat() if t.deadline else None,
                }
                for t in found
            ],
            "drafts": [
                {
                    "id": r.id,
                    "name": (r.fields or {}).get("name") or "",
                    "taskId": r.task_id,
                }
                for r in mine
            ],
        }
    )


class ReadIn(BaseModel):
    draft: int | None = None
    task: int | None = None


@router.post("/agent/read", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_read(
    body: ReadIn, resolver: ActorResolverDep, spaces: Spaces, db=Depends(get_db)
) -> dict:
    """`cheese_read` of `drafts/<id>` or `spaces/<id>/tasks/<id>`: a draft, or a published 题目, in 芝士's words."""
    user_id = await _person(resolver)
    if body.draft is not None:
        row = await drafts.mine(db, user_id, body.draft)
        return ok({"text": await _draft_text(db, spaces, row, user_id)})
    if body.task is None:
        raise BadRequestError(say("draftNeedsTarget"))
    task = await TaskService.of(db).get_task(body.task)
    if task is None:
        raise NotFoundError.for_resource("task", body.task)
    await ensure_task_readable(session=db, task=task, user_id=user_id)
    terms = await _space_terms(db, spaces, task.space_id, user_id)
    teaches = await task_seats.teaches(db, task, user_id)
    text = drafts.published_view(
        task.id,
        task.name,
        await _task_as_form(db, task, private=teaches),
        terms,
        private=teaches,
    )
    pending = await drafts.for_task(db, user_id, task.id)
    if pending is not None:
        text += f"\n这道题有未保存的修改：草稿 {pending.id}。"
    return ok({"text": text})


class WriteIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    draft: int | None = None
    task: int | None = None
    space: int | None = None
    version: int | None = None
    source: str = ""
    notes: list[dict] = Field(default_factory=list)


@router.post("/agent/write", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_write(
    body: WriteIn,
    resolver: ActorResolverDep,
    spaces: Spaces,
    questions: Questions,
    db=Depends(get_db),
) -> dict:
    """cheese_problem(action=write): write fields of a draft — a new one in a space, the
    one named, or the person's changes to a published 题目 — and tell the
    page."""
    user_id = await _person(resolver)
    asked = await _question(resolver, questions)
    conversation = _conversation_of(asked)
    created = False
    if body.draft is not None:
        row = await drafts.mine(db, user_id, body.draft)
    elif body.task is not None:
        task = await _editable_task(db, body.task, user_id)
        before = await drafts.for_task(db, user_id, task.id)
        row = await _edit_draft(db, task, user_id, conversation)
        created = before is None
    else:
        if body.space is None:
            raise BadRequestError(say("draftNeedsTarget"))
        await _publishable_space(db, body.space, user_id)
        row = await drafts.start(
            db,
            user_id=user_id,
            space_id=body.space,
            source=body.source,
            conversation_id=conversation,
            fields={"submitterType": "USER", "submissionSchema": []},
        )
        created = True
    terms = await _space_terms(db, spaces, row.space_id, user_id)
    args: dict[str, Any] = dict(body.model_extra or {})
    files = (
        await _own_files(db, args["attachments"], user_id)
        if "attachments" in args
        else None
    )
    starter = (
        (await _own_files(db, [args["starter"]], user_id))[0]
        # 0 (or nothing) is "no starter code" (the tool's own wording).
        if args.get("starter")
        else None
    )
    removed: list[str] = []
    fields = drafts.from_agent(
        args, terms, row.fields or {}, files, removed, starter=starter
    )
    written = drafts.cheese_writes(
        row, fields, read_at=None if created else body.version
    )
    if row.conversation_id is None and conversation is not None:
        row.conversation_id = conversation
    notes = [
        {"field": str(n.get("field") or ""), "text": str(n.get("text") or "")}
        for n in body.notes
        if isinstance(n, dict) and n.get("text")
    ]
    # What saving the draft's stages as they now stand would do to the 题目's
    # takers, whatever field this write changed: told on the change card and to
    # 芝士 before anything is saved, as the edit page does. None: not a change
    # to a published 题目, or the writer may no longer change it.
    stage_effect = (
        await _stage_effect(db, row, user_id) if row.task_id is not None else None
    )
    if removed:
        # Never silently: the person reads on the change card what went with
        # the stages 芝士 removed, before anything is saved to the 题目.
        notes.append(
            {
                "field": "stages",
                "text": str(say("draftStageRemovedRows", names="、".join(removed))),
            }
        )
    told = await _report(
        db,
        resolver,
        row.id,
        {
            "draft": row.id,
            "taskId": row.task_id,
            "created": created,
            "version": row.version,
            "name": (row.fields or {}).get("name") or "",
            # How the task starts once this write is in: the card says the
            # team lock and the dates the way that mode means them.
            "startMode": (row.fields or {}).get("startMode") or "DIRECT",
            "changed": {
                k: {"before": b, "after": a} for k, (b, a) in written.changed.items()
            },
            "kept": written.kept,
            "notes": notes,
            # Replaces, never adds to, what an earlier write in the answer said:
            # the card shows the effect of the draft as it now stands.
            **(
                {"stageEffect": [s.descriptor() for s in stage_effect]}
                if stage_effect is not None
                else {}
            ),
        },
    )
    await db.commit()
    if asked is not None and told is not None:
        await questions.publish(asked, "change", told)
    return ok(
        {
            "draft": row.id,
            "created": created,
            "version": row.version,
            "changed": sorted(written.changed),
            "kept": written.kept,
            "missing": drafts.missing(
                row, (await drafts.published_items(db, [row])).get(row.task_id or 0, 0)
            ),
            # Submission items and rubric lines that left with a removed stage.
            "removed": removed,
            # Who saving the draft's stages would fail or take out of failed,
            # or why the save would refuse them; absent when neither.
            **(
                {"stageEffect": "；".join(str(s) for s in stage_effect)}
                if stage_effect
                else {}
            ),
            # A template's skeleton, for 芝士 to fill under its headings.
            **(
                {"description": drafts.markdown_of(row.fields or {})}
                if "template" in args
                else {}
            ),
        }
    )


async def _stage_effect(db, row, user_id: int) -> list[NoticeText] | None:
    """What saving the draft's stages onto its 题目 would do to the claims, as
    sentences (``stage_change_effect``, the edit page's own check): the save's
    refusal when it would refuse them; empty when nobody would cross the
    failed line, or when the draft's stages are the 题目's own. None for
    someone who may not change the 题目 now — a draft outlives its writer's
    rights, and the counts are the 题目's, like its private guidance
    (``_editable_task``)."""
    task = await TaskService.of(db).get_task(row.task_id)
    if task is None or not await task_seats.teaches(db, task, user_id):
        return None
    stages = drafts.published_form(row).get("stages") or []
    if _stage_rows(stages) == _stage_rows(
        stages_to_api(await list_stages(db, task.id))
    ):
        return []
    try:
        effect = await stage_change_effect(db, row.task_id, stage_lines(stages))
    except BadRequestError as refused:
        # Every refusal of a stage change is a platform sentence (``say``).
        if not isinstance(refused.message, NoticeText):
            raise
        return [refused.message]
    said: list[NoticeText] = []
    if effect.failing:
        said.append(say("stageEffectFailing", n=effect.failing))
    if effect.reopening:
        said.append(say("stageEffectReopening", n=effect.reopening))
    return said


def _stage_rows(stages: list) -> list[tuple]:
    """Stages as the save compares them: id, name, what it asks for, due."""
    return [
        (
            s.get("id") or None,
            str(s.get("name") or "").strip(),
            str(s.get("description") or "").strip(),
            s.get("dueAt"),
        )
        for s in stages
        if isinstance(s, dict)
    ]


async def _own_files(db, given, user_id: int) -> list[dict]:
    """The files 芝士 attaches to a 题目: ones the person handed over, with the
    name and size the page lists."""
    files = _attachment_service(db)
    out: list[dict] = []
    for raw in given or []:
        try:
            file_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(say("draftFileNotYours")) from exc
        attachment = await files.get(file_id)
        if not files.is_uploader(attachment, user_id):
            raise ForbiddenError(say("draftFileNotYours"))
        meta = attachment.meta or {}
        if all(f["id"] != attachment.id for f in out):
            out.append(
                {
                    "id": attachment.id,
                    "name": str(meta.get("filename") or attachment.id),
                    "size": int(meta.get("size") or 0),
                }
            )
    return out


class PublishIn(BaseModel):
    draft: int


@router.post("/agent/publish", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_publish(
    body: PublishIn,
    resolver: ActorResolverDep,
    service: Tasks,
    questions: Questions,
    db=Depends(get_db),
) -> dict:
    """cheese_problem(action=publish): publish a draft as the person would."""
    user_id = await _person(resolver)
    row = await drafts.mine(db, user_id, body.draft)
    missing = drafts.missing(
        row, (await drafts.published_items(db, [row])).get(row.task_id or 0, 0)
    )
    if missing:
        raise BadRequestError(say("draftIncomplete", fields=", ".join(missing)))
    editing = row.task_id is not None
    answer = await _publish(db, service, row, user_id)
    task = answer["task"]
    asked = await _question(resolver, questions)
    if asked is not None:
        await questions.publish(
            asked,
            "published",
            {
                "draft": body.draft,
                "task": {"id": task["id"], "name": task.get("name") or ""},
                "spaceId": row.space_id,
                "saved": editing,
                "approved": task.get("approved"),
            },
        )
    return ok(
        {
            "task": {
                "id": task["id"],
                "name": task.get("name"),
                "approved": task.get("approved"),
            },
            "saved": editing,
        }
    )


def _pictures() -> bool:
    """Whether the model a person's 芝士 answers with can look at a page."""
    return gateway_catalog.sees_pictures(model_roles.model("assistant_model"))


class FileIn(BaseModel):
    #: A file the person handed over (``files/<id>``) ...
    file: int | None = None
    #: ... or one of a 题目's materials (``spaces/<s>/tasks/<task>/files/<name>``).
    task: int | None = None
    name: str | None = None
    pages: str | None = None


@router.post("/agent/file", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_file(body: FileIn, resolver: ActorResolverDep, db=Depends(get_db)):
    """`cheese_read` of `files/<id>` (a file the person handed over) or of
    `spaces/<s>/tasks/<t>/files/<name>` (one of the materials of a 题目 they
    may download), as text, a range of pages at a time."""
    user_id = await _person(resolver)
    if body.task is not None and body.name:
        task = await TaskService.of(db).get_task(body.task)
        if task is None:
            raise NotFoundError.for_resource("task", body.task)
        await ensure_task_readable(session=db, task=task, user_id=user_id)
        await package.refuse_unless_downloader(db, task, user_id)
        content = await library_records.read(db, package.shelf(task.id), body.name)
        return ok(
            read_document(content, body.name, pages=body.pages, pictures=_pictures())
        )
    if body.file is None:
        raise BadRequestError(say("draftNeedsTarget"))
    files = _attachment_service(db)
    attachment = await files.get(body.file)
    if not files.is_uploader(attachment, user_id):
        raise ForbiddenError(say("draftFileNotYours"))
    content, filename, _ = await files.download(body.file)
    return ok(read_document(content, filename, pages=body.pages, pictures=_pictures()))


class MaterialsIn(BaseModel):
    space: int
    material: int | None = None
    pages: str | None = None


@router.post("/agent/materials", include_in_schema=False, dependencies=[ROUTE_DECIDES])
async def agent_materials(
    body: MaterialsIn, resolver: ActorResolverDep, db=Depends(get_db)
):
    """`cheese_read` of `spaces/<id>` or `spaces/<id>/materials/<id>`: the space's materials the person can read, or
    one of them as text."""
    user_id = await _person(resolver)
    materials = SpaceMaterialService(session=db, storage=get_storage_backend())
    if body.material is None:
        listed = await materials.list_for_space(space_id=body.space, user_id=user_id)
        return ok(
            {
                "materials": [
                    {"id": m.get("id"), "name": m.get("name"), "size": m.get("size")}
                    for m in listed["materials"]
                ]
            }
        )
    content, name, _ = await materials.download(
        space_id=body.space, user_id=user_id, material_id=body.material
    )
    await db.commit()
    return ok(read_document(content, name, pages=body.pages, pictures=_pictures()))


async def _question(resolver: ActorResolverDep, questions: Questions):
    """The question being answered with this credential, when it is one of a
    person's: what changed is told on its stream."""
    claims = resolver.delegation()
    if claims is None or not claims.work:
        return None
    try:
        asked = await questions.get(claims.work)
    except RuntimeError:  # no Valkey: nobody to tell
        return None
    if asked is None or asked.kind != assistant.KIND:
        return None
    return asked


async def _report(db, resolver: ActorResolverDep, draft_id: int, payload: dict):
    """Keep 芝士's write into a draft, to be shown and undone; what the page is
    told of it. None when no question of a person's made it."""
    claims = resolver.delegation()
    if claims is None or claims.user_id is None or not claims.work:
        return None
    row = await changes.report(
        db,
        user_id=claims.user_id,
        work=claims.work,
        kind="draft",
        target=draft_id,
        payload=payload,
    )
    return changes.view(row)


def _draft_parts(payload: dict) -> list[str]:
    """A draft write's parts: the fields as 芝士 names them, each one or a few
    form fields (``AGENT_FIELDS``)."""
    changed = payload.get("changed") or {}
    parts: dict[tuple[str, ...], str] = {}
    for key, targets in drafts.AGENT_FIELDS.items():
        # Several of 芝士's fields write the one guidance: it is one part.
        if any(t in changed for t in targets):
            parts.setdefault(targets, key)
    return list(parts.values())


async def _put_back(db, resolver, same, parts: list[str], redo: bool) -> dict:
    """Put fields of a draft back as they were before the answer wrote them
    (the first write's ``before``), or as it left them (the last's ``after``),
    as the person's own write: a field it puts back is the person's again."""
    user_id = await _person(resolver)
    row = await drafts.mine(db, user_id, int(same[0].target))
    order = list(reversed(same)) if redo else list(same)
    fields: dict[str, Any] = {}
    for part in parts:
        for target in drafts.AGENT_FIELDS[part]:
            for change in order:
                values = (change.payload.get("changed") or {}).get(target)
                if values is not None:
                    fields[target] = values["after" if redo else "before"]
                    break
    if fields:
        drafts.person_writes(row, fields)
    return {"draft": row.id, "version": row.version, "fields": fields}


changes.register("draft", parts=_draft_parts, apply=_put_back)


def _conversation_of(asked):
    if asked is None:
        return None
    try:
        return uuid.UUID(asked.key)
    except ValueError:
        return None
