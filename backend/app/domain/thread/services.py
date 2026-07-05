"""Business logic for threads and memberships."""

from app.core.errors import BadRequestError, NotFoundError
from app.domain.thread.models import (
    AttentionPolicy,
    MemberKind,
    MemberRole,
    Thread,
    ThreadKind,
    ThreadMembership,
)
from app.domain.thread.repositories import ThreadRepository


class ThreadService:
    def __init__(self, repo: ThreadRepository) -> None:
        self._repo = repo

    async def _require_thread(self, thread_id: int) -> Thread:
        thread = await self._repo.get_by_id(thread_id)
        if thread is None:
            raise NotFoundError(f"Thread {thread_id} not found")
        return thread

    async def create_thread(
        self,
        *,
        project_id: int,
        created_by_id: int,
        title: str = "",
        kind: ThreadKind = ThreadKind.GENERAL,
        parent_thread_id: int | None = None,
    ) -> Thread:
        # A subthread must sit under a parent in the same project (the tree
        # stays within one aggregate root).
        if parent_thread_id is not None:
            parent = await self._require_thread(parent_thread_id)
            if parent.project_id != project_id:
                raise BadRequestError("parent thread belongs to another project")
        return await self._repo.create(
            project_id=project_id,
            created_by_id=created_by_id,
            title=title,
            kind=kind,
            parent_thread_id=parent_thread_id,
        )

    async def get_thread(self, thread_id: int) -> Thread:
        return await self._require_thread(thread_id)

    async def list_children(self, thread_id: int) -> list[Thread]:
        await self._require_thread(thread_id)
        return await self._repo.list_children(thread_id)

    async def list_by_project(self, project_id: int) -> list[Thread]:
        return await self._repo.list_by_project(project_id)

    async def add_member(
        self,
        *,
        thread_id: int,
        member_id: int,
        member_kind: MemberKind,
        role: MemberRole = MemberRole.MEMBER,
        attention_policy: AttentionPolicy = AttentionPolicy.MENTION_ONLY,
        attention_window_seconds: int | None = None,
    ) -> ThreadMembership:
        await self._require_thread(thread_id)
        existing = await self._repo.get_membership(
            thread_id=thread_id, member_id=member_id, member_kind=member_kind
        )
        if existing is not None:
            raise BadRequestError("member already in thread")
        if (
            attention_policy is AttentionPolicy.IDLE_WINDOW
            and attention_window_seconds is None
        ):
            raise BadRequestError("idle_window policy requires attention_window_seconds")
        return await self._repo.add_membership(
            thread_id=thread_id,
            member_id=member_id,
            member_kind=member_kind,
            role=role,
            attention_policy=attention_policy,
            attention_window_seconds=attention_window_seconds,
        )

    async def list_members(self, thread_id: int) -> list[ThreadMembership]:
        await self._require_thread(thread_id)
        return await self._repo.list_members(thread_id)
