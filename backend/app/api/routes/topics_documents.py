"""The documents a room works on: recompute, convert, and track their changes.

Second slice of `app/api/routes/topics.py` (arch review C-backend.md section 3.3),
the one `topics_attachments.py` began. topics.py is 3,812 lines against a
1,500-line cap that only ratchets down, and the four routes that move here are one
concept: a document a room is *producing*, rather than a file it is passing
around. The sandbox carries no LibreOffice and has no root to install one, so
recomputing a workbook's formulas happens where the previews are rendered
(`POST /topics/{topic_id}/documents/recalc`); the same service converts a
pre-2007 Office file, or hands back the PDF of a Word file, which is how a room
sees its own layout before delivering it (`POST .../documents/convert`); and the
tracked changes in a `.docx` are listed (`GET .../documents/revisions`) and
decided (`POST .../documents/revisions`) without Word.

Where the shared names went. The names these four routes read from topics.py now
have homes of their own: `clean_artifact_path` and `MAX_ARTIFACT_BYTES` are the
room-file rules in `app.domain.project.room_files`, `source_bytes` is the API
read-orchestration helper in `app.api.routes.topics_file_sources`, and the binding
check became `TaskService.require_source_in_room`. `DbSession` and `_actor_in_place`
are still imported from topics.py, the shape `admin_models.py` uses for `DbSession`.
`_row_numbers` and `_document_bytes` are the opposite case: nothing outside these
four routes names them, so they move with them. None of the new homes imports this
module, so there is no cycle.

The domain imports named only here leave topics.py with them -- the three
`app.domain.documents` modules, `content_version`, and the `ConflictError` /
`SystemBusyError` pair. The names its remaining handlers still read
(`ValidationError`, `library`, `room_files`, `settings`, `TopicService`) stay
there, and this module imports its own from wherever they are defined.

Ordering. This module sorts after `topics.py` (`.` < `_`), so its router mounts
after that file's, and the four paths keep their relative order among themselves.
Nothing registered earlier can shadow them: no route anywhere has a parameter
where `documents` sits, so `/topics/{topic_id}/documents/recalc` and
`.../documents/convert` have no parameterized route to lose to, and the two
`.../documents/revisions` methods are the only routes of that shape. A request per
path confirms each still reaches its own handler.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the same
`APIRouter(prefix="/topics", tags=["topics"])` is all it takes.
"""

import base64
import binascii
import uuid
from typing import Literal

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession, _actor_in_place
from app.api.routes.topics_file_sources import source_bytes
from app.core.config import settings
from app.core.errors import ConflictError, SystemBusyError, ValidationError
from app.core.sentences import exception_text, say
from app.domain.documents.convert import (
    ConvertFailed,
    ConvertUnavailable,
    convert,
    upgraded_name,
)
from app.domain.documents.revisions import (
    RevisionsFailed,
    RevisionsUnsupported,
    decide,
    revisions_in,
)
from app.domain.documents.spreadsheet import (
    SpreadsheetRecalcFailed,
    SpreadsheetRecalcUnavailable,
    recalculate,
)
from app.domain.library import service as library
from app.domain.project import room_files
from app.domain.project.room_files import MAX_ARTIFACT_BYTES, clean_artifact_path
from app.domain.room_task.services import TaskService
from app.domain.textfile import content_version
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/documents/recalc")
async def recalc_spreadsheet(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Recompute a workbook's formulas — used by `cheese recalc`.

    The room cannot do this itself: recomputing means loading the workbook in
    something that evaluates formulas, and the sandbox image carries no
    LibreOffice and has no root to install one. The platform already runs one
    for previews, so this is the path to it.

    The workbook travels in the body rather than being read from the worktree,
    because a room on a remote machine has no file here — the same reason
    `artifact` takes `content_b64`.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    path = clean_artifact_path(body.get("path") or "")
    raw = _document_bytes(body, place.project_id, topic_id, path)
    try:
        book, bad = await recalculate(raw, path, settings.office_render_endpoint)
    except SpreadsheetRecalcUnavailable as exc:
        raise SystemBusyError(exception_text(exc)) from exc
    except SpreadsheetRecalcFailed as exc:
        raise ValidationError(exception_text(exc)) from exc
    return ok(
        {
            "path": path,
            "content_b64": base64.b64encode(book).decode(),
            "errors": [cell.as_dict() for cell in bad],
        }
    )


@router.post("/{topic_id}/documents/convert")
async def convert_document(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Convert one document to another format — used by `cheese convert`.

    The pre-2007 binary formats are the reason this exists: a room cannot read
    or write them at all, so the alternative is asking the user to open Office
    himself. It also gets a room a PDF of a Word file, which is how it looks at
    its own layout before delivering it.
    """
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    path = clean_artifact_path(body.get("path") or "")
    target = str(body.get("to") or "").strip()
    raw = _document_bytes(body, place.project_id, topic_id, path)
    try:
        made = await convert(raw, path, target, settings.office_render_endpoint)
    except ConvertUnavailable as exc:
        raise SystemBusyError(exception_text(exc)) from exc
    except ConvertFailed as exc:
        raise ValidationError(exception_text(exc)) from exc
    return ok(
        {
            "path": upgraded_name(path, target.lower().lstrip(".")),
            "content_b64": base64.b64encode(made).decode(),
        }
    )


@router.get("/{topic_id}/documents/revisions")
async def list_document_revisions(
    topic_id: uuid.UUID,
    path: str,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
    source: Literal["live", "committed"] = "live",
) -> dict:
    """The tracked changes in a `.docx`, one row per decision a reader makes.

    The preview beside this list already draws the changes — LibreOffice renders
    insertions and deletions, measured — so the list is not there to show them.
    It is there to act on them: a reader can accept or reject one without
    opening Word.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = clean_artifact_path(path)
    if task is not None:
        await TaskService(db).require_source_in_room(topic_id, task)
    raw = await source_bytes(db, topic.project_id, topic_id, clean, task, source)
    try:
        found = revisions_in(raw, clean)
    except RevisionsUnsupported as exc:
        raise ValidationError(exception_text(exc)) from exc
    except RevisionsFailed as exc:
        raise ValidationError(exception_text(exc)) from exc
    return ok(
        {
            "path": clean,
            "version": content_version(raw),
            "revisions": [r.as_dict() for r in found],
        }
    )


@router.post("/{topic_id}/documents/revisions")
async def decide_document_revisions(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """Accept or reject tracked changes, and write the document back.

    Accepting an insertion removes its wrapper and keeps the text; accepting a
    deletion removes the text with it; rejecting does the opposite. All of it is
    a determinate transformation of the XML, so the file the reader downloads
    afterwards is the file Word would have produced.

    ``version`` is the one the list was read at. A room re-publishing the
    artifact between that read and this write would otherwise lose its newer
    copy to a decision taken against the older one, so a moved file is a
    conflict here rather than an overwrite.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    clean = clean_artifact_path(body.get("path") or "")
    if library.library_name(clean) is not None:
        # 资料库那一份是用户给进来的原件，只读：这里写回去就是在他没要求的时候改了
        # 他的文件，而且改的是所有房间都在引用的那一份。修订仍然读得出来（清单那一
        # 栏照常列），能做的只是不动它。
        raise ValidationError(say("libraryOriginalReadOnly"))
    accept = _row_numbers(body.get("accept"), "accept")
    reject = _row_numbers(body.get("reject"), "reject")
    expected = str(body.get("version") or "")
    if not expected:
        raise ValidationError(say("checklistVersionMissing"))
    source = body.get("task")
    task = uuid.UUID(str(source)) if source else None
    if task is not None:
        await TaskService(db).require_source_in_room(topic_id, task)
    raw = await source_bytes(db, topic.project_id, topic_id, clean, task)
    actual = content_version(raw)
    if actual != expected:
        raise ConflictError(
            say("revisionListStale"),
            data={"path": clean, "version": actual},
        )
    try:
        made, left = decide(raw, clean, accept=accept, reject=reject)
    except RevisionsUnsupported as exc:
        raise ValidationError(exception_text(exc)) from exc
    except RevisionsFailed as exc:
        raise ValidationError(exception_text(exc)) from exc
    if task is not None:
        from app.domain.repository.forge_files import ProjectFiles

        await ProjectFiles(db, topic.project_id, task).write_bytes(
            clean, made, expected
        )
    else:
        await room_files.save_room_file(
            db,
            project_id=topic.project_id,
            room_id=topic_id,
            path=clean,
            data=made,
            author=actor.handle,
            author_kind="agent" if actor.via == "cheese" else "human",
            source="editor",
            note="处理修订",
            base_version=expected,
        )
    return ok(
        {
            "path": clean,
            "version": content_version(made),
            "revisions": [r.as_dict() for r in left],
        }
    )


def _row_numbers(raw, field: str) -> list[int]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError(say("rowNumbersNotArray", field=field))
    out: list[int] = []
    for item in raw:
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            raise ValidationError(say("rowNumbersInvalid", field=field))
        out.append(item)
    return out


def _document_bytes(
    body: dict, project_id: uuid.UUID, topic_id: uuid.UUID, path: str
) -> bytes:
    """The document a request is about, from the body or from the workspace.

    A room on a remote machine has no file here, so it sends the bytes; the
    panel is reading a file the platform already holds. Same reason
    `artifact` takes `content_b64`.
    """
    encoded = body.get("content_b64")
    if isinstance(encoded, str):
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValidationError(say("contentB64Invalid")) from exc
    else:
        raw = library.read_room_file(project_id, topic_id, path)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValidationError(
            say("fileOverLimit", mb=MAX_ARTIFACT_BYTES // (1024 * 1024))
        )
    return raw
