from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.space.models import Space, SpaceCategory, SpaceAdminRole, SpaceAdminRelation
from app.domain.space.repositories import (
    SpaceRepository,
    SpaceCategoryRepository,
    SpaceUserRankRepository,
    SpaceAdminRelationRepository,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.task.repositories import TaskRepository


class SpaceService:
    def __init__(
        self,
        repo: SpaceRepository,
        category_repo: SpaceCategoryRepository,
        admin_repo: SpaceAdminRelationRepository | None = None,
        rank_repo: SpaceUserRankRepository | None = None,
        task_repo: "TaskRepository | None" = None,
    ) -> None:
        self._repo = repo
        self._category_repo = category_repo
        self._admin_repo = admin_repo
        self._rank_repo = rank_repo
        self._task_repo = task_repo

    # ------------------------------------------------------------------
    # Basic queries
    # ------------------------------------------------------------------

    async def get_space(self, space_id: int) -> Space | None:
        return await self._repo.get_by_id(space_id)

    async def exists_by_name(self, name: str) -> bool:
        return await self._repo.exists_by_name(name)

    async def list_spaces(self, *, limit: int, offset: int = 0) -> Sequence[Space]:
        return await self._repo.list_spaces(limit=limit, offset=offset)

    async def count_spaces(self) -> int:
        return await self._repo.count_spaces()

    async def list_categories(
        self,
        space_id: int,
        *,
        include_archived: bool = False,
    ) -> Sequence[SpaceCategory]:
        return await self._category_repo.list_categories_for_space(
            space_id=space_id,
            include_archived=include_archived,
        )

    async def get_category_detail(self, *, space_id: int, category_id: int) -> SpaceCategory:
        return await self._get_category(space_id, category_id)

    async def get_user_rank(self, space_id: int, user_id: int | None) -> int | None:
        if user_id is None or self._rank_repo is None:
            return None
        return await self._rank_repo.get_rank(space_id=space_id, user_id=user_id)

    # ------------------------------------------------------------------
    # Space lifecycle
    # ------------------------------------------------------------------

    async def create_space(
        self,
        *,
        name: str,
        intro: str,
        description: str,
        avatar_id: int | None,
        enable_rank: bool,
        owner_id: int,
        announcements: list,
        task_templates: list,
    ) -> Space:
        self._validate_strings(name=name)
        if not isinstance(intro, str) or not isinstance(description, str):
            raise BadRequestError("intro and description must be strings")
        normalized_announcements = self._normalize_json_list(announcements)
        normalized_templates = self._normalize_json_list(task_templates)

        space = await self._repo.create_space(
            name=name.strip(),
            intro=intro.strip(),
            description=description.strip(),
            avatar_id=avatar_id,
            enable_rank=enable_rank,
            announcements=normalized_announcements,
            task_templates=normalized_templates,
        )

        # Default category "General"
        default_category = await self._category_repo.create_category(
            space_id=space.id,
            name="General",
            description="Auto generated default category",
            display_order=0,
        )
        space.default_category_id = default_category.id
        await self._repo.save(space)

        if self._admin_repo is not None:
            await self._admin_repo.add_admin(
                space_id=space.id,
                user_id=owner_id,
                role=SpaceAdminRole.OWNER,
            )

        return space

    async def update_space(
        self,
        *,
        space_id: int,
        actor_user_id: int | None,
        name: str | None = None,
        intro: str | None = None,
        description: str | None = None,
        avatar_id: int | None = None,
        enable_rank: bool | None = None,
        announcements: list | None = None,
        task_templates: list | None = None,
        default_category_id: int | None = None,
    ) -> Space:
        space = await self._get_space_or_error(space_id)
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)

        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise BadRequestError("Space name cannot be empty")
            space.name = name.strip()
        if intro is not None:
            if not isinstance(intro, str):
                raise BadRequestError("Space intro must be string")
            space.intro = intro.strip()
        if description is not None:
            if not isinstance(description, str):
                raise BadRequestError("Space description must be string")
            space.description = description.strip()
        if avatar_id is not None:
            space.avatar_id = avatar_id
        if enable_rank is not None:
            space.enable_rank = bool(enable_rank)
        if announcements is not None:
            space.announcements = self._normalize_json_list(announcements)
        if task_templates is not None:
            space.task_templates = self._normalize_json_list(task_templates)
        if default_category_id is not None:
            category = await self._category_repo.get_by_id(default_category_id)
            if category is None or category.space_id != space_id:
                raise NotFoundError(
                    "Space category not found",
                    data={"spaceId": space_id, "categoryId": default_category_id},
                )
            space.default_category_id = default_category_id

        space.updated_at = datetime.utcnow()
        return await self._repo.save(space)

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def create_category(
        self,
        *,
        space_id: int,
        name: str,
        description: str | None,
        display_order: int,
        actor_user_id: int | None,
    ) -> SpaceCategory:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        if not name.strip():
            raise BadRequestError("Category name cannot be empty")
        if await self._category_repo.exists_unarchived_name(space_id, name.strip()):
            raise BadRequestError("Category name already exists")
        return await self._category_repo.create_category(
            space_id=space_id,
            name=name.strip(),
            description=description.strip() if isinstance(description, str) else description,
            display_order=display_order,
        )

    async def update_category(
        self,
        *,
        space_id: int,
        category_id: int,
        actor_user_id: int | None,
        name: str | None = None,
        description: str | None = None,
        display_order: int | None = None,
        archived: bool | None = None,
    ) -> SpaceCategory:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        category = await self._get_category(space_id, category_id)

        if name is not None:
            if not name.strip():
                raise BadRequestError("Category name cannot be empty")
            if name.strip() != category.name and await self._category_repo.exists_unarchived_name(
                space_id, name.strip()
            ):
                raise BadRequestError("Category name already exists")
            category.name = name.strip()
        if description is not None:
            category.description = description.strip()
        if display_order is not None:
            category.display_order = display_order
        if archived is not None:
            category.archived_at = datetime.utcnow() if archived else None

        category.updated_at = datetime.utcnow()
        return await self._category_repo.save(category)

    async def delete_category(
        self,
        *,
        space_id: int,
        category_id: int,
        actor_user_id: int | None,
    ) -> None:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        category = await self._get_category(space_id, category_id)

        space = await self._repo.get_by_id(space_id)
        if space is None:
            raise NotFoundError.for_resource("space", space_id)

        if space.default_category_id == category_id:
            raise BadRequestError("Cannot delete the default category.")

        if self._task_repo is not None:
            task_count = await self._task_repo.count_tasks(
                space_id=space_id,
                category_id=category_id,
            )
            if task_count > 0:
                raise BadRequestError("Cannot delete a category that contains tasks.")

        category.deleted_at = datetime.utcnow()
        category.updated_at = datetime.utcnow()
        await self._category_repo.save(category)

    async def set_category_archived(
        self,
        *,
        space_id: int,
        category_id: int,
        archived: bool,
        actor_user_id: int | None,
    ) -> SpaceCategory:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        category = await self._get_category(space_id, category_id)
        category.archived_at = datetime.utcnow() if archived else None
        category.updated_at = datetime.utcnow()
        return await self._category_repo.save(category)

    # ------------------------------------------------------------------
    # Admin relations
    # ------------------------------------------------------------------

    async def list_admins(self, space_id: int) -> Sequence[SpaceAdminRelation]:
        if self._admin_repo is None:
            return []
        return await self._admin_repo.list_admins(space_id)

    async def add_admin(
        self,
        *,
        space_id: int,
        target_user_id: int,
        role: SpaceAdminRole,
        actor_user_id: int | None,
    ) -> None:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=False)
        if self._admin_repo is None:
            raise BadRequestError("Space admin repository unavailable")
        existing = await self._admin_repo.get_relation(space_id, target_user_id)
        if existing and existing.deleted_at is None:
            if role is SpaceAdminRole.OWNER and existing.role != SpaceAdminRole.OWNER.value:
                await self._promote_admin_to_owner(space_id, existing)
                return
            raise BadRequestError("User already has a space role")
        if role is SpaceAdminRole.OWNER:
            current_owner = await self._admin_repo.get_owner(space_id)
            if current_owner is not None:
                current_owner.role = SpaceAdminRole.ADMIN.value
                current_owner.updated_at = datetime.utcnow()
                await self._admin_repo.save(current_owner)
        await self._admin_repo.add_admin(
            space_id=space_id,
            user_id=target_user_id,
            role=role,
        )

    async def remove_admin(
        self,
        *,
        space_id: int,
        target_user_id: int,
        actor_user_id: int | None,
    ) -> None:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=False)
        if self._admin_repo is None:
            raise BadRequestError("Space admin repository unavailable")
        relation = await self._admin_repo.get_relation(space_id, target_user_id)
        if relation is None:
            raise NotFoundError("Space admin relation not found")
        if relation.role == SpaceAdminRole.OWNER.value:
            raise BadRequestError("Cannot remove space owner. Transfer ownership instead.")
        await self._admin_repo.remove_admin(relation)

    async def delete_space(self, *, space_id: int, actor_user_id: int | None) -> None:
        space = await self._get_space_or_error(space_id)
        await self._ensure_admin(space_id, actor_user_id, allow_admin=False)
        space.deleted_at = datetime.utcnow()
        space.updated_at = datetime.utcnow()
        await self._repo.save(space)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_admin(
        self,
        space_id: int,
        user_id: int | None,
        *,
        allow_admin: bool,
    ) -> None:
        if self._admin_repo is None:
            return
        if user_id is None:
            raise ForbiddenError("Authentication required")
        relation = await self._admin_repo.get_relation(space_id, user_id)
        if relation is None:
            raise ForbiddenError("Only space admins can perform this action")
        if not allow_admin and relation.role != SpaceAdminRole.OWNER.value:
            raise ForbiddenError("Only space owner can perform this action")

    async def _get_space_or_error(self, space_id: int) -> Space:
        space = await self._repo.get_by_id(space_id)
        if space is None:
            raise NotFoundError("Resource space not found", data={"type": "space", "id": space_id})
        return space

    async def _promote_admin_to_owner(
        self,
        space_id: int,
        relation: SpaceAdminRelation,
    ) -> None:
        current_owner = await self._admin_repo.get_owner(space_id)
        if current_owner is not None:
            current_owner.role = SpaceAdminRole.ADMIN.value
            current_owner.updated_at = datetime.utcnow()
            await self._admin_repo.save(current_owner)
        relation.role = SpaceAdminRole.OWNER.value
        relation.updated_at = datetime.utcnow()
        await self._admin_repo.save(relation)

    async def _get_category(self, space_id: int, category_id: int) -> SpaceCategory:
        category = await self._category_repo.get_by_id(category_id)
        if category is None or category.space_id != space_id:
            raise NotFoundError(
                "Space category not found",
                data={"spaceId": space_id, "categoryId": category_id},
            )
        return category

    @staticmethod
    def _validate_strings(**kwargs: str) -> None:
        for key, value in kwargs.items():
            if not isinstance(value, str) or not value.strip():
                raise BadRequestError(f"{key} cannot be empty")

    @staticmethod
    def _normalize_json_list(value: list | None) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        raise BadRequestError("Expected list value")
