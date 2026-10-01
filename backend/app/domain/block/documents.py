"""The living document's block effects, all inside the caller's transaction."""

import difflib
import html
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domain.block.about import EventAbout, landing
from app.domain.block.doc_tree import PARAGRAPH, markdown_to_nodes
from app.domain.block.models import AGENT_NOTICE_META_KEY, AuthorType, Block, BlockKind
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


class DocumentWriter:
    def __init__(self, session: AsyncSession, summarize: Callable[[str, str], str]):
        self._session = session
        self._blocks = BlockRepository(session)
        self._summarize = summarize

    async def edit_doc(
        self,
        *,
        room_id: uuid.UUID,
        project_id: uuid.UUID,
        content: str,
        author: str,
        expected_version: int,
        author_type: AuthorType = AuthorType.participant,
        operation_id: uuid.UUID | None = None,
    ) -> tuple[Block, Block | None]:
        """改文档即指令 (eval B2): upsert the topic's living doc and drop a
        '编辑了文档' event into the conversation. The agent reads the latest doc
        on its next turn, so the edit acts as an instruction.

        ``expected_version`` is the ``doc_version`` the writer read; ``0`` says
        it expects no doc to exist yet. A write based on any other version is
        refused, because this doc is only ever written whole — 芝士 setting back
        a document it assembled from a ten-minute-old copy erases whatever a
        person typed in between, with nothing left to recover it from.

        The refusal comes BEFORE any of the write's effects: no node tree, no
        '编辑了文档' event. A rejected write that still announced itself would
        put a change in the room that is not in the document.
        """
        journal = DocumentJournal(self._session)
        await journal.lock(room_id)
        doc = await self._blocks.doc_root(room_id)
        previous_content = doc.content if doc is not None else ""
        if doc is not None:
            await self._session.refresh(doc)
            previous_content = doc.content
            if doc.doc_version != expected_version:
                raise _doc_conflict(doc.doc_version)
            await journal.seed_existing(
                room_id=room_id,
                document_id=doc.id,
                version=doc.doc_version,
                content=doc.content,
                actor=doc.author,
            )
            updated = await self._blocks.set_doc_content(
                doc, content, expected_version=expected_version
            )
            if updated is None:
                raise _doc_conflict(doc.doc_version)
            doc = updated
        else:
            if expected_version != 0:
                raise _doc_conflict(0)
            doc = await self._blocks.add(
                project_id=project_id,
                topic_id=room_id,
                author=author,
                author_type=author_type,
                content=content,
                kind=BlockKind.doc,
            )
        # The root records the latest editor; unchanged nodes keep their author,
        # and _sync_doc_nodes attributes only newly written nodes to this editor.
        doc.author = author
        doc.author_type = author_type
        # B1: also sync the structured node tree (struct_parent children) so the
        # doc's blocks get stable ids for cross-view highlight / comments later.
        await self._sync_doc_nodes(doc, content)
        # Append-only conversation event (spec H1): the doc edit is visible.
        before_lines = _doc_edit_lines(previous_content)
        after_lines = _doc_edit_lines(content)
        if before_lines == after_lines:
            await journal.append(
                room_id=room_id,
                document_id=doc.id,
                version=doc.doc_version,
                content=doc.content,
                actor=author,
                base_version=expected_version,
                operation_id=operation_id,
            )
            return doc, None
        # A human actor is emitted as the structured <@handle> token so the
        # client renders it as a clickable mention chip (resolving handle→name
        # via the roster) — NOT prose we later pattern-match. 芝士 is one familiar
        # name whichever 分身 wrote it: each authors under its own
        # ``cheese-<topic hex>`` handle, which is not what a reader should see.
        by_agent = looks_like_agent_handle(author)
        actor = say("actorCheese") if by_agent else f"<@{author}>"
        # What the same event says to 芝士, written here because this is the code
        # that moved the document. It locates the change and does NOT carry it:
        # a document pushed into a running turn displaces the work instead of
        # informing it, and the doc is one call away.
        #
        # 芝士's own edit is not news to 芝士 — it wrote the version it is holding.
        for_agent = (
            None
            if by_agent
            else (
                f"实况文档已被 {actor} 更新至第 {doc.doc_version} 版，"
                f"{self._summarize(previous_content, content)}。"
                "你此前读到的内容可能已经过期。继续依据它工作或写回之前，"
                "先用 cheese_doc_get 重新读取；基于旧版本的写回会被拒绝。"
            )
        )
        landed = landing(
            EventAbout.room,
            project_id=project_id,
            room_id=room_id,
        )
        notice = await self._blocks.add(
            project_id=landed.project_id,
            topic_id=landed.topic_id,
            task_id=landed.task_id,
            author=author,
            author_type=AuthorType.platform,
            content=say("docEdited", actor=actor),
            kind=BlockKind.event,
            refs=[str(doc.id)],
            # action:"doc" → the client renders the 看文档 link on this SAME
            # line — one event vocabulary for humans and 芝士 alike.
            meta={
                "platform": True,
                "action": "doc",
                "doc_version": doc.doc_version,
                AGENT_NOTICE_META_KEY: for_agent,
                "detail_label": say("labelDocEditDiff"),
                "detail": "\n".join(
                    difflib.unified_diff(
                        before_lines,
                        after_lines,
                        fromfile="修改前",
                        tofile="修改后",
                        lineterm="",
                    )
                ),
            },
        )
        await journal.append(
            room_id=room_id,
            document_id=doc.id,
            version=doc.doc_version,
            content=doc.content,
            actor=author,
            base_version=expected_version,
            operation_id=operation_id,
            event_id=notice.id,
        )
        return doc, notice

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
