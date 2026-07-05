"""Integration tests for the document projection (docs-out over blocks)."""

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.services import BlockService
from app.domain.document.services import DocumentService

PROJECT = 95001


class TestDocumentProjection:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.blocks = BlockService(BlockRepository(db_session))
        self.docs = DocumentService(BlockRepository(db_session))

    def test_tree_and_render(self):
        async def _run():
            root = await self.blocks.create_block(
                project_id=PROJECT, content="Title", author_id=1, kind=BlockKind.DOCUMENT
            )
            s1 = await self.blocks.create_block(
                project_id=PROJECT,
                content="Section 1",
                author_id=1,
                kind=BlockKind.DOCUMENT,
                struct_parent_id=root.id,
            )
            await self.blocks.create_block(
                project_id=PROJECT,
                content="para",
                author_id=1,
                kind=BlockKind.DOCUMENT,
                struct_parent_id=s1.id,
            )
            await self.blocks.create_block(
                project_id=PROJECT,
                content="Section 2",
                author_id=1,
                kind=BlockKind.DOCUMENT,
                struct_parent_id=root.id,
            )
            tree = await self.docs.get_tree(root.id)
            text = await self.docs.render_text(root.id)
            return tree, text

        tree, text = self.portal.call(_run)
        assert tree.block.content == "Title"
        assert [c.block.content for c in tree.children] == ["Section 1", "Section 2"]
        assert tree.children[0].children[0].block.content == "para"
        assert text == "Title\n  Section 1\n    para\n  Section 2"

    def test_missing_root_raises(self):
        async def _run():
            await self.docs.get_tree(9_999_999)

        with pytest.raises(NotFoundError):
            self.portal.call(_run)
