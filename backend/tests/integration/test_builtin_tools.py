"""Integration tests for the built-in agent tools (DB-backed), invoked through
the registry with an injected ProjectActor + ToolContext."""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.authorization.authorizer import ProjectActor
from app.agent.tools.builtin import builtin_registry
from app.agent.tools.context import ToolContext
from app.core.errors import ForbiddenError
from app.domain.block.models import AuthorKind, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.notification.models import Notification, NotificationType
from app.domain.project.repositories import ProjectRepository
from app.domain.thread.models import ThreadKind
from app.domain.thread.repositories import ThreadRepository
from tests.integration.conftest import unique_int

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

    def test_request_human_decision_notifies_leader(self):
        leader_id = unique_int(1_000_000, 9_000_000)  # unique receiver avoids dedup

        async def _run():
            now = datetime.now(UTC)
            project = await ProjectRepository(self.db).create_project(
                name="p",
                description="d",
                color_code="#ffffff",
                team_id=1,
                leader_id=leader_id,
                start_date=now,
                end_date=now,
            )
            actor = ProjectActor(kind="agent", actor_id=77, project_id=project.id)
            out = await builtin_registry.invoke(
                "request_human_decision",
                {"question": "should we ship?"},
                injected={ProjectActor: actor, ToolContext: self.ctx},
            )
            rows = (
                await self.db.execute(
                    select(Notification).where(Notification.receiver_id == leader_id)
                )
            ).scalars().all()
            return out, list(rows)

        out, rows = self.portal.call(_run)
        assert out["notified_user_id"] == leader_id
        assert any(r.type == NotificationType.AGENT_DECISION_REQUEST for r in rows)

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
