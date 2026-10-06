"""The living document's block effects, all inside the caller's transaction."""

import difflib
import html
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.sentences import NoticeList, listing, say
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    agent_notice,
    consumed_turn,
)
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.living_doc.doc_tree import PARAGRAPH, markdown_to_nodes
from app.domain.living_doc.models import Document
from app.domain.living_doc.services import DocumentJournal, Documents


def _doc_edit_lines(content: str) -> list[str]:
    # Empty editor paragraphs are layout, not a contribution. Keep the saved
    # document intact; omit only empty prose nodes from conversation evidence.
    return "\n\n".join(
        node.content
        for node in markdown_to_nodes(content)
        if node.node_type != PARAGRAPH or html.unescape(node.content).strip()
    ).splitlines()


def _doc_conflict(current_version: int) -> ConflictError:
    """The living doc moved under a writer. The current version rides along so
    the caller can re-read and rebase without a second round trip."""
    return ConflictError(
        say("liveDocStale"),
        data={"doc_version": current_version},
    )


#: How long the last "编辑了文档" stays open to the next edit (see record).
DOC_NOTICE_MERGE_WINDOW = timedelta(minutes=10)
_DOC_FROM_VERSION_KEY = "doc_from_version"
_DOC_ACTORS_KEY = "doc_actors"
#: An edit the room's agent made for someone: who asked, and the passages it
#: changed (``[{old, new}]``, every edit of the run).
DOC_REQUESTED_BY_KEY = "doc_requested_by"
DOC_EDITS_KEY = "doc_edits"
#: Changes proposed, not made: the suggestion ids the line covers.
DOC_SUGGESTED_KEY = "doc_suggested"
DOC_SUGGESTIONS_KEY = "doc_suggestions"


def _actor_label(handles: list[str]) -> NoticeList:
    """Everyone in an edit run, as the event names them. A human is the
    structured <@handle> token the client renders as a mention chip; 芝士 is
    one familiar name whichever 分身 wrote (each authors under its own
    ``cheese-<topic hex>`` handle, which is not what a reader should see)."""
    names: list[str] = []
    for handle in handles:
        name = say("actorCheese") if looks_like_agent_handle(handle) else f"<@{handle}>"
        if name not in names:
            names.append(name)
    return listing(names)


def _notice_kind(meta: dict) -> str:
    """The three lines a document change can be: somebody edited it
    ("plain"), the agent edited it for somebody ("requested"), changes were
    proposed ("suggested"). A run extends a line of its own kind only."""
    if meta.get(DOC_SUGGESTED_KEY):
        return "suggested"
    if meta.get(DOC_REQUESTED_BY_KEY):
        return "requested"
    return "plain"


def persisted_notice(block: Block) -> str | None:
    return agent_notice(block)


class DocumentWriter:
    def __init__(self, session: AsyncSession, summarize: Callable[[str, str], str]):
        self._session = session
        self._blocks = BlockRepository(session)
        self._docs = Documents(session)
        self._summarize = summarize
        #: Whether the last record extended an earlier notice instead of
        #: adding one (the caller announces an update, not a new line).
        self.notice_merged = False

    async def seed(self, doc: Document, content: str) -> None:
        """The first version of a room's document, written by the platform
        (a newborn room's brief). The caller holds the document's lock."""
        if doc.version != 0:
            raise _doc_conflict(doc.version)
        written = await self._docs.write(
            doc, content, author="system", expected_version=0
        )
        if written is None:
            raise _doc_conflict(doc.version)
        await DocumentJournal(self._session).append(
            document_id=doc.id,
            version=doc.version,
            content=content,
            actor="system",
            base_version=0,
        )

    async def record(
        self,
        doc: Document,
        *,
        content: str,
        actors: list[str],
        operation_id: uuid.UUID | None = None,
        quiet: bool = False,
        requested_by: str | None = None,
        edits: list[dict] | None = None,
    ) -> tuple[Document | None, Block | None]:
        """改文档即指令 (eval B2): record a new version of a room's living doc
        and drop a '编辑了文档' event into the conversation. The agent reads the
        latest doc on its next turn, so the edit acts as an instruction.

        ``content`` is what the collaboration service exported from the live
        document, and ``actors`` are the handles whose changes it holds, the
        one with the most changes first. The caller holds the document's
        journal lock: the live document is the only writer, so there is no
        base version to compare here — whatever the service stores is the
        document.

        Returns ``(None, None)`` when there is nothing to record: the text did
        not change. A ``quiet`` version gets no conversation event, like a
        brief seed, and leaves the document's author as it was.

        ``requested_by`` is the person an edit was made for (the agent edited
        at their request), and ``edits`` the passages it changed: the line
        then says who asked, and only extends a line about the same person's
        request.
        """
        if not actors:
            raise ValueError("a document version needs the actor who wrote it")
        room_id = doc.room_id
        project_id = doc.project_id
        self.notice_merged = False
        author = actors[0]
        journal = DocumentJournal(self._session)
        await self._session.refresh(doc)
        previous_content = doc.content
        if previous_content == content:
            return None, None
        base_version = doc.version
        # A quiet version only respells the text and is nobody's edit: the
        # document stays its last editor's, respelled nodes included. Unchanged
        # nodes keep their author either way.
        updated = await self._docs.write(
            doc,
            content,
            author=doc.author if quiet else author,
            expected_version=base_version,
            keep_author=quiet,
        )
        if updated is None:
            raise _doc_conflict(doc.version)
        doc = updated
        # Append-only conversation event (spec H1): the doc edit is visible in
        # the room whose document it is. A document in no room has no
        # conversation to tell; its history is the record.
        before_lines = _doc_edit_lines(previous_content)
        after_lines = _doc_edit_lines(content)
        if quiet or room_id is None or before_lines == after_lines:
            await journal.append(
                document_id=doc.id,
                version=doc.version,
                content=doc.content,
                actor=author,
                base_version=base_version,
                operation_id=operation_id,
                requested_by=requested_by,
            )
            return doc, None
        landed = landing(
            EventAbout.room,
            project_id=project_id,
            room_id=room_id,
        )
        # A run of edits is one line in the conversation, not one per store:
        # the last "编辑了文档" is extended while nothing else has been said
        # since and it was last touched within the window. It then names
        # everyone in the run, and its diff spans the run.
        earlier = await self._mergeable_notice(
            landed, doc.id, kind="requested" if requested_by else "plain"
        )
        if earlier is not None and (earlier.meta or {}).get(DOC_REQUESTED_BY_KEY) != (
            requested_by or None
        ):
            earlier = None
        everyone = list(actors)
        from_version = base_version
        from_content = previous_content
        changed = [{"old": e["old"], "new": e["new"]} for e in edits or []]
        if earlier is not None:
            meta = earlier.meta or {}
            everyone = list(dict.fromkeys([*meta.get(_DOC_ACTORS_KEY, []), *actors]))
            changed = [*meta.get(DOC_EDITS_KEY, []), *changed]
            from_version = int(meta[_DOC_FROM_VERSION_KEY])
            from_content = (
                await journal.version_content(doc.id, from_version)
                if from_version
                else ""
            ) or ""
        actor = _actor_label(everyone)
        # What the same event says to 芝士, written here because this is the code
        # that moved the document. It locates THIS change and does NOT carry it:
        # a document pushed into a running turn displaces the work instead of
        # informing it, and the doc is one call away. It is about this store,
        # not the run the line covers: 芝士 is told to re-read either way, and
        # where the newest change is is what it can use.
        #
        # 芝士's own edit is not news to 芝士 — it wrote the version it is
        # holding — but it does not silence a person's edit in the same line
        # that 芝士 has not heard about yet.
        editors = _actor_label(list(actors))
        for_agent = (
            None
            if all(looks_like_agent_handle(handle) for handle in actors)
            else (
                f"实况文档已被 {editors} 更新至第 {doc.version} 版，"
                f"{self._summarize(previous_content, content)}。"
                "你此前读到的内容可能已经过期。继续依据它工作或写回之前，"
                "先用 cheese_doc_get 重新读取；基于旧版本的写回会被拒绝。"
            )
        )
        if for_agent is None and earlier is not None and consumed_turn(earlier) is None:
            for_agent = agent_notice(earlier)
        # action:"doc" → the client renders the 看文档 link on this SAME line —
        # one event vocabulary for humans and 芝士 alike.
        meta = {
            "platform": True,
            "action": "doc",
            "doc_version": doc.version,
            _DOC_FROM_VERSION_KEY: from_version,
            _DOC_ACTORS_KEY: everyone,
            AGENT_NOTICE_META_KEY: for_agent,
            "detail_label": say("labelDocEditDiff"),
            "detail": "\n".join(
                difflib.unified_diff(
                    _doc_edit_lines(from_content),
                    after_lines,
                    fromfile="修改前",
                    tofile="修改后",
                    lineterm="",
                )
            ),
        }
        if requested_by:
            meta[DOC_REQUESTED_BY_KEY] = requested_by
            meta[DOC_EDITS_KEY] = changed
            line = say("docEditedFor", requester=f"<@{requested_by}>", agent=actor)
        else:
            line = say("docEdited", actor=actor)
        if earlier is not None:
            earlier.author = author
            earlier.content = line
            earlier.meta = meta
            # It is the newest thing said in the room, and says so.
            earlier.created_at = datetime.now(UTC)
            self.notice_merged = True
            notice = earlier
        else:
            notice = await self._blocks.add(
                project_id=landed.project_id,
                conversation_id=landed.conversation_id,
                author=author,
                author_type=AuthorType.platform,
                content=line,
                kind=BlockKind.event,
                refs=[str(doc.id)],
                meta=meta,
            )
        await journal.append(
            document_id=doc.id,
            version=doc.version,
            content=doc.content,
            actor=author,
            base_version=base_version,
            operation_id=operation_id,
            event_id=notice.id,
            requested_by=requested_by,
        )
        await self._session.flush()
        return doc, notice

    async def suggest(
        self,
        doc: Document,
        *,
        actor: str,
        suggestion_ids: list[str],
        reason: str | None = None,
    ) -> Block | None:
        """Tell the room ``actor`` proposed changes to the document.

        A suggestion is not a version — the text is what it was until someone
        accepts it — so this only writes the line, and extends the last one
        when it is the same writer's suggestions within the window.
        """
        self.notice_merged = False
        if doc.room_id is None or not suggestion_ids:
            return None
        landed = landing(
            EventAbout.room, project_id=doc.project_id, room_id=doc.room_id
        )
        earlier = await self._mergeable_notice(landed, doc.id, kind="suggested")
        if earlier is not None and (earlier.meta or {}).get(_DOC_ACTORS_KEY) != [actor]:
            earlier = None
        ids = list(suggestion_ids)
        if earlier is not None:
            ids = [*(earlier.meta or {}).get(DOC_SUGGESTIONS_KEY, []), *ids]
        meta = {
            "platform": True,
            "action": "doc",
            "doc_version": doc.version,
            _DOC_FROM_VERSION_KEY: doc.version,
            _DOC_ACTORS_KEY: [actor],
            DOC_SUGGESTED_KEY: True,
            DOC_SUGGESTIONS_KEY: ids,
            "doc_reason": reason,
        }
        line = say("docSuggested", agent=_actor_label([actor]), n=len(ids))
        if earlier is not None:
            earlier.content = line
            earlier.meta = meta
            earlier.created_at = datetime.now(UTC)
            self.notice_merged = True
            await self._session.flush()
            return earlier
        return await self._blocks.add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=actor,
            author_type=AuthorType.platform,
            content=line,
            kind=BlockKind.event,
            refs=[str(doc.id)],
            meta=meta,
        )

    async def _mergeable_notice(
        self, landed, document_id: uuid.UUID, *, kind: str
    ) -> Block | None:
        """The room's last line, if it is this document's notice of the same
        ``kind`` (see ``_notice_kind``), recent enough to extend."""
        last = await self._blocks.latest_for_topic(landed.conversation_id)
        if last is None or last.kind != BlockKind.event:
            return None
        meta = last.meta or {}
        if meta.get("action") != "doc" or str(document_id) not in (last.refs or []):
            return None
        if _DOC_FROM_VERSION_KEY not in meta or _DOC_ACTORS_KEY not in meta:
            return None
        if _notice_kind(meta) != kind:
            return None
        if datetime.now(UTC) - last.created_at > DOC_NOTICE_MERGE_WINDOW:
            return None
        return last


#: A line about a document of the project's own: which one (``{id, title}``).
#: Such a document is in no room, so the room 芝士 worked in when it made or
#: changed it is told, and the line carries the document to open.
DOC_CARD_KEY = "document"
DOC_CREATED_KEY = "doc_created"


async def tell_room_of_document(
    session: AsyncSession,
    *,
    room_id: uuid.UUID,
    doc: Document,
    actor: str,
    task_id: uuid.UUID | None = None,
    created: bool = False,
    edits: list[dict] | None = None,
    suggested: list[str] | None = None,
) -> tuple[Block, bool]:
    """Put in the room (or its task ``task_id``) that ``actor`` made or changed
    ``doc``, a document of the project's own: a line with the document on it, to
    open beside the conversation. ``suggested`` are the ids of changes proposed
    instead of made. A run of changes of one kind to the same document by the same
    actor is one line, as with a room's document. Returns the line and whether it
    extended the last one."""
    blocks = BlockRepository(session)
    landed = landing(
        EventAbout.task if task_id is not None else EventAbout.room,
        project_id=doc.project_id,
        room_id=room_id,
        task_id=task_id,
    )
    changed = [{"old": e["old"], "new": e["new"]} for e in edits or []]
    last = await blocks.latest_for_topic(landed.conversation_id)
    meta_of_last = (last.meta or {}) if last is not None else {}
    if (
        last is not None
        and last.kind == BlockKind.event
        and (meta_of_last.get(DOC_CARD_KEY) or {}).get("id") == str(doc.id)
        and last.author == actor
        and bool(meta_of_last.get(DOC_SUGGESTED_KEY)) == bool(suggested)
        and datetime.now(UTC) - last.created_at <= DOC_NOTICE_MERGE_WINDOW
    ):
        extended = {
            **meta_of_last,
            DOC_CARD_KEY: {"id": str(doc.id), "title": doc.title or ""},
            DOC_EDITS_KEY: [*meta_of_last.get(DOC_EDITS_KEY, []), *changed],
        }
        if suggested:
            extended[DOC_SUGGESTIONS_KEY] = [
                *meta_of_last.get(DOC_SUGGESTIONS_KEY, []),
                *suggested,
            ]
        last.meta = extended
        last.created_at = datetime.now(UTC)
        await session.flush()
        return last, True
    title = doc.title or say("docUntitled")
    who = _actor_label([actor])
    if created:
        line = say("docCreatedInLibrary", actor=who, title=title)
    elif suggested:
        line = say("docSuggestedInLibrary", actor=who, title=title)
    else:
        line = say("docEditedInLibrary", actor=who, title=title)
    block = await blocks.add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author=actor,
        author_type=AuthorType.platform,
        content=line,
        kind=BlockKind.event,
        refs=[str(doc.id)],
        meta={
            "platform": True,
            "action": "doc",
            DOC_CARD_KEY: {"id": str(doc.id), "title": doc.title or ""},
            DOC_CREATED_KEY: created,
            DOC_EDITS_KEY: changed,
            **(
                {DOC_SUGGESTED_KEY: True, DOC_SUGGESTIONS_KEY: list(suggested)}
                if suggested
                else {}
            ),
        },
    )
    return block, False
