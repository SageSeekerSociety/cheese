"""Integration tests for the block domain (DB-backed).

Exercises the append-only dual tree (reply_to / struct_parent), the
cross-project boundary invariant, and typed refs, through BlockService +
BlockRepository against a real transaction.
"""

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.domain.block.models import AuthorKind, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.services import BlockService

PROJECT_A = 90001
PROJECT_B = 90002


class TestBlockDomain:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.svc = BlockService(BlockRepository(db_session))

    def test_conversation_tree(self):
        async def _run():
            root = await self.svc.create_block(
                project_id=PROJECT_A, content="hello", author_id=1, thread_id=5
            )
            reply = await self.svc.create_block(
                project_id=PROJECT_A,
                content="hi back",
                author_id=2,
                author_kind=AuthorKind.AGENT,
                thread_id=5,
                reply_to_id=root.id,
            )
            replies = await self.svc.list_replies(root.id)
            return root, reply, replies

        root, reply, replies = self.portal.call(_run)
        assert reply.reply_to_id == root.id
        assert reply.author_kind == AuthorKind.AGENT.value
        assert [b.id for b in replies] == [reply.id]

    def test_document_tree(self):
        async def _run():
            doc = await self.svc.create_block(
                project_id=PROJECT_A, content="doc root", author_id=1, kind=BlockKind.DOCUMENT
            )
            child = await self.svc.create_block(
                project_id=PROJECT_A,
                content="section",
                author_id=1,
                kind=BlockKind.DOCUMENT,
                struct_parent_id=doc.id,
            )
            children = await self.svc.list_children(doc.id)
            return doc, child, children

        doc, child, children = self.portal.call(_run)
        assert child.struct_parent_id == doc.id
        assert [b.id for b in children] == [child.id]

    def test_parent_must_be_same_project(self):
        async def _run():
            root = await self.svc.create_block(
                project_id=PROJECT_A, content="a", author_id=1
            )
            # Replying from another project must be rejected.
            await self.svc.create_block(
                project_id=PROJECT_B, content="cross", author_id=1, reply_to_id=root.id
            )

        with pytest.raises(BadRequestError):
            self.portal.call(_run)

    def test_refs(self):
        async def _run():
            a = await self.svc.create_block(project_id=PROJECT_A, content="a", author_id=1)
            b = await self.svc.create_block(project_id=PROJECT_A, content="b", author_id=1)
            await self.svc.add_ref(
                from_block_id=a.id, to_target_type="block", to_target_id=b.id, rel="mentions"
            )
            refs = await self.svc.list_refs(a.id)
            return refs, b

        refs, b = self.portal.call(_run)
        assert len(refs) == 1
        assert refs[0].to_target_id == b.id
        assert refs[0].rel == "mentions"

    def test_thread_cursor(self):
        async def _run():
            ids = []
            for i in range(3):
                blk = await self.svc.create_block(
                    project_id=PROJECT_A, content=f"m{i}", author_id=1, thread_id=77
                )
                ids.append(blk.id)
            # after the first block's id we should only see the last two.
            tail = await self.svc.list_thread(77, after_id=ids[0])
            return ids, tail

        ids, tail = self.portal.call(_run)
        assert [b.id for b in tail] == ids[1:]
