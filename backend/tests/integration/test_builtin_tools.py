"""Integration tests for the built-in agent tools (DB-backed), invoked through
the registry with an injected ProjectActor + ToolContext."""

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.authorization.authorizer import ProjectActor
from app.agent.tools.builtin import builtin_registry
from app.agent.tools.context import ToolContext
from app.core.errors import ForbiddenError
from app.domain.block.models import AuthorKind, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.thread.models import ThreadKind
from app.domain.thread.repositories import ThreadRepository

PROJECT = 94001
OTHER_PROJECT = 94002


class TestBuiltinTools:
    @pytest.fixture(autouse=True)
    def setup(self, db_session: AsyncSession, _portal: BlockingPortal):
        self.db = db_session
        self.portal = _portal
        self.ctx = ToolContext(session=db_session)

    def test_post_message_creates_agent_block_in_project(self):
        async def _run():
            thread = await ThreadRepository(self.db).create(
                project_id=PROJECT, created_by_id=1, kind=ThreadKind.GENERAL
            )
            actor = ProjectActor(kind="agent", actor_id=77, project_id=PROJECT)
            out = await builtin_registry.invoke(
                "post_message",
                {"thread_id": thread.id, "content": "done, see doc"},
                injected={ProjectActor: actor, ToolContext: self.ctx},
            )
            block = await BlockRepository(self.db).get_by_id(out["block_id"])
            return thread, block

        thread, block = self.portal.call(_run)
        assert block is not None
        assert block.thread_id == thread.id
        assert block.project_id == PROJECT
        assert block.author_id == 77
        assert block.author_kind == AuthorKind.AGENT.value
        assert block.kind == BlockKind.MESSAGE.value

    def test_post_message_rejects_foreign_thread(self):
        async def _run():
            # thread lives in OTHER_PROJECT; actor belongs to PROJECT.
            thread = await ThreadRepository(self.db).create(
                project_id=OTHER_PROJECT, created_by_id=1
            )
            actor = ProjectActor(kind="agent", actor_id=77, project_id=PROJECT)
            await builtin_registry.invoke(
                "post_message",
                {"thread_id": thread.id, "content": "hi"},
                injected={ProjectActor: actor, ToolContext: self.ctx},
            )

        with pytest.raises(ForbiddenError):
            self.portal.call(_run)

    def test_write_document_uses_actor_project(self):
        async def _run():
            actor = ProjectActor(kind="user", actor_id=3, project_id=PROJECT)
            out = await builtin_registry.invoke(
                "write_document",
                {"content": "# findings"},
                injected={ProjectActor: actor, ToolContext: self.ctx},
            )
            return await BlockRepository(self.db).get_by_id(out["block_id"])

        block = self.portal.call(_run)
        assert block is not None
        assert block.project_id == PROJECT  # derived from actor, not args
        assert block.author_kind == AuthorKind.USER.value
        assert block.kind == BlockKind.DOCUMENT.value
