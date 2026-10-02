"""The living document's block effects, all inside the caller's transaction."""

import difflib
import html
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domain.block.about import EventAbout, landing
from app.domain.block.doc_tree import PARAGRAPH, markdown_to_nodes
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    AuthorType,
    Block,
    BlockKind,
    agent_notice,
    consumed_turn,
)
from app.domain.block.notice_text import say
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.living_doc.services import DocumentJournal


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
        "实况文档已经被改过了，你手上这份是旧的",
        data={"doc_version": current_version},
    )


#: How long the last "编辑了文档" stays open to the next edit (see record).
DOC_NOTICE_MERGE_WINDOW = timedelta(minutes=10)
_DOC_FROM_VERSION_KEY = "doc_from_version"
_DOC_ACTORS_KEY = "doc_actors"


def _actor_label(handles: list[str]) -> str:
    """Everyone in an edit run, as the event names them. A human is the
    structured <@handle> token the client renders as a mention chip; 芝士 is
    one familiar name whichever 分身 wrote (each authors under its own
    ``cheese-<topic hex>`` handle, which is not what a reader should see)."""
    names: list[str] = []
    for handle in handles:
        name = say("actorCheese") if looks_like_agent_handle(handle) else f"<@{handle}>"
        if name not in names:
            names.append(name)
    return "、".join(names)


def persisted_notice(block: Block) -> str | None:
    return agent_notice(block)


class DocumentWriter:
    def __init__(self, session: AsyncSession, summarize: Callable[[str, str], str]):
        self._session = session
        self._blocks = BlockRepository(session)
        self._summarize = summarize
        #: Whether the last record extended an earlier notice instead of
        #: adding one (the caller announces an update, not a new line).
        self.notice_merged = False

    async def seed(
        self, *, room_id: uuid.UUID, project_id: uuid.UUID, content: str
    ) -> None:
        journal = DocumentJournal(self._session)
        await journal.lock(room_id)
        if await self._blocks.doc_root(room_id) is not None:
            raise _doc_conflict(1)
        doc = await self._blocks.add(
            project_id=project_id,
            topic_id=room_id,
            author="system",
            author_type=AuthorType.platform,
            content=content,
            kind=BlockKind.doc,
        )
        await self._sync_doc_nodes(doc, content)
        await journal.append(
            room_id=room_id,
            document_id=doc.id,
            version=doc.doc_version,
            content=content,
            actor="system",
            base_version=0,
        )

    async def record(
        self,
        *,
        room_id: uuid.UUID,
        project_id: uuid.UUID,
        content: str,
        actors: list[str],
        operation_id: uuid.UUID | None = None,
        quiet: bool = False,
    ) -> tuple[Block | None, Block | None]:
        """改文档即指令 (eval B2): record a new version of the room's living doc
        and drop a '编辑了文档' event into the conversation. The agent reads the
        latest doc on its next turn, so the edit acts as an instruction.

        ``content`` is what the collaboration service exported from the live
        document, and ``actors`` are the handles whose changes it holds, the
        one with the most changes first. The caller holds the room's journal
        lock: the live document is the only writer, so there is no base version
        to compare here — whatever the service stores is the document.

        Returns ``(None, None)`` when there is nothing to record: the text did
        not change, or a room with no document stored an empty one. A
        ``quiet`` version gets no conversation event, like a brief seed, and
        leaves the document's author as it was.
        """
        if not actors:
            raise ValueError("a document version needs the actor who wrote it")
        self.notice_merged = False
        author = actors[0]
        journal = DocumentJournal(self._session)
        doc = await self._blocks.doc_root(room_id)
        previous_content = ""
        if doc is not None:
            await self._session.refresh(doc)
            previous_content = doc.content
            if previous_content == content:
                return None, None
            base_version = doc.doc_version
            await journal.seed_existing(
                room_id=room_id,
                document_id=doc.id,
                version=doc.doc_version,
                content=doc.content,
                actor=doc.author,
            )
            updated = await self._blocks.set_doc_content(
                doc, content, expected_version=base_version
            )
            if updated is None:
                raise _doc_conflict(doc.doc_version)
            doc = updated
        else:
            if not content.strip():
                return None, None
            base_version = 0
            doc = await self._blocks.add(
                project_id=project_id,
                topic_id=room_id,
                author=author,
                author_type=AuthorType.participant,
                content=content,
                kind=BlockKind.doc,
            )
        # The root records the latest editor; unchanged nodes keep their author,
        # and _sync_doc_nodes attributes only newly written nodes to this editor.
        # A quiet version only respells the text and is nobody's edit: the
        # document stays its last editor's, respelled nodes included.
        if not quiet:
            doc.author = author
            doc.author_type = AuthorType.participant
        # B1: also sync the structured node tree (struct_parent children) so the
        # doc's blocks get stable ids for cross-view highlight / comments later.
        await self._sync_doc_nodes(doc, content)
        # Append-only conversation event (spec H1): the doc edit is visible.
        before_lines = _doc_edit_lines(previous_content)
        after_lines = _doc_edit_lines(content)
        if quiet or before_lines == after_lines:
            await journal.append(
                room_id=room_id,
                document_id=doc.id,
                version=doc.doc_version,
                content=doc.content,
                actor=author,
                base_version=base_version,
                operation_id=operation_id,
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
        earlier = await self._mergeable_notice(landed, doc.id)
        everyone = list(actors)
        from_version = base_version
        from_content = previous_content
        if earlier is not None:
            meta = earlier.meta or {}
            everyone = list(dict.fromkeys([*meta.get(_DOC_ACTORS_KEY, []), *actors]))
            from_version = int(meta[_DOC_FROM_VERSION_KEY])
            from_content = (
                await journal.version_content(room_id, from_version)
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
                f"实况文档已被 {editors} 更新至第 {doc.doc_version} 版，"
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
            "doc_version": doc.doc_version,
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
        if earlier is not None:
            earlier.author = author
            earlier.content = say("docEdited", actor=actor)
            earlier.meta = meta
            # It is the newest thing said in the room, and says so.
            earlier.created_at = datetime.now(UTC)
            self.notice_merged = True
            notice = earlier
        else:
            notice = await self._blocks.add(
                project_id=landed.project_id,
                topic_id=landed.topic_id,
                task_id=landed.task_id,
                author=author,
                author_type=AuthorType.platform,
                content=say("docEdited", actor=actor),
                kind=BlockKind.event,
                refs=[str(doc.id)],
                meta=meta,
            )
        await journal.append(
            room_id=room_id,
            document_id=doc.id,
            version=doc.doc_version,
            content=doc.content,
            actor=author,
            base_version=base_version,
            operation_id=operation_id,
            event_id=notice.id,
        )
        await self._session.flush()
        return doc, notice

    async def _mergeable_notice(self, landed, document_id: uuid.UUID) -> Block | None:
        """The room's last line, if it is this document's edit notice, recent
        enough to extend."""
        last = await self._blocks.latest_for_topic(
            landed.topic_id, task_id=landed.task_id
        )
        if last is None or last.kind != BlockKind.event:
            return None
        meta = last.meta or {}
        if meta.get("action") != "doc" or str(document_id) not in (last.refs or []):
            return None
        if _DOC_FROM_VERSION_KEY not in meta or _DOC_ACTORS_KEY not in meta:
            return None
        if datetime.now(UTC) - last.created_at > DOC_NOTICE_MERGE_WINDOW:
            return None
        return last

    async def _sync_doc_nodes(self, root: Block, content: str) -> None:
        """Reconcile the living doc's node tree (B1) with `content` via a
        block-level diff so unchanged nodes keep their ids (anchors survive an
        edit). Re-setting the same markdown is a no-op."""
        new_nodes = markdown_to_nodes(content)
        existing = await self._blocks.list_doc_nodes(root.topic_id)
        matcher = difflib.SequenceMatcher(
            a=[b.content for b in existing],
            b=[n.content for n in new_nodes],
            autojunk=False,
        )
        # Reuse existing block ids wherever content is unchanged (equal runs).
        reuse: dict[int, Block] = {}
        for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
            if tag == "equal":
                for off in range(i2 - i1):
                    reuse[j1 + off] = existing[i1 + off]
        kept_ids = {b.id for b in reuse.values()}
        for b in existing:
            if b.id not in kept_ids:
                await self._blocks.delete(b)
        for idx, node in enumerate(new_nodes):
            order = float(idx)
            block = reuse.get(idx)
            if block is not None:
                if block.struct_order != order or block.node_type != node.node_type:
                    await self._blocks.update_node(
                        block, node_type=node.node_type, struct_order=order
                    )
            else:
                await self._blocks.add(
                    project_id=root.project_id,
                    topic_id=root.topic_id,
                    author=root.author,
                    author_type=root.author_type,
                    content=node.content,
                    kind=BlockKind.doc_node,
                    struct_parent=root.id,
                    node_type=node.node_type,
                    struct_order=order,
                )
