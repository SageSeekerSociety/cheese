import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.shell.catalog import DEFAULT_CATEGORY_SHELL_NAME, is_course_shell
from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceCategory,
    SpaceDomainGroup,
    SpaceInviteCode,
    SpaceMember,
)
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceClassificationTopicsRepository,
    SpaceDomainGroupDomainRepository,
    SpaceDomainGroupRepository,
    SpaceInviteCodeRepository,
    SpaceMemberRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.tag.models import Tag

if TYPE_CHECKING:
    from app.domain.task.repositories import TaskRepository


#: What a code minted at space-creation time is worth. Enough to bring in a
#: class; an admin who wants a different budget mints one explicitly.
DEFAULT_SPACE_INVITE_CODE_MAX_USES = 50

#: No 0/O/1/I/L — these codes get read aloud and typed in by hand.
_INVITE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_INVITE_CODE_LENGTH = 10


class SpaceService:
    def __init__(
        self,
        repo: SpaceRepository,
        category_repo: SpaceCategoryRepository,
        admin_repo: SpaceAdminRelationRepository | None = None,
        rank_repo: SpaceUserRankRepository | None = None,
        task_repo: "TaskRepository | None" = None,
        classification_topics_repo: SpaceClassificationTopicsRepository | None = None,
        domain_group_repo: SpaceDomainGroupRepository | None = None,
        domain_group_domain_repo: SpaceDomainGroupDomainRepository | None = None,
        member_repo: SpaceMemberRepository | None = None,
        invite_code_repo: SpaceInviteCodeRepository | None = None,
    ) -> None:
        self._repo = repo
        self._category_repo = category_repo
        self._admin_repo = admin_repo
        self._rank_repo = rank_repo
        self._task_repo = task_repo
        self._classification_topics_repo = classification_topics_repo
        self._domain_group_repo = domain_group_repo
        self._domain_group_domain_repo = domain_group_domain_repo
        self._member_repo = member_repo
        self._invite_code_repo = invite_code_repo

    # ------------------------------------------------------------------
    # What a 题目板 is
    # ------------------------------------------------------------------

    async def default_category_shells(
        self, *, space_ids: Sequence[int]
    ) -> dict[int, str | None]:
        """The 壳 each of these 题目板's default 分组 declares.

        The answer is a name, not a verdict: `app.domain.shell.catalog` owns
        which names mean 「this board is a course」, so callers ask it rather
        than comparing strings themselves.
        """
        return await self._repo.default_category_shells(space_ids=space_ids)

    async def is_course(self, *, space_id: int) -> bool:
        shells = await self.default_category_shells(space_ids=[space_id])
        return is_course_shell(shells.get(space_id))

    # ------------------------------------------------------------------
    # Classification topics
    # ------------------------------------------------------------------

    async def list_classification_topics(self, space_id: int) -> list[Tag]:
        if self._classification_topics_repo is None:
            return []
        return await self._classification_topics_repo.list_topics_for_space(space_id)

    async def list_classification_topics_for_spaces(
        self, space_ids: Sequence[int]
    ) -> dict[int, list[Tag]]:
        if self._classification_topics_repo is None or not space_ids:
            return {}
        return await self._classification_topics_repo.list_topics_for_spaces(space_ids)

    async def replace_classification_topics(
        self, *, space_id: int, topic_ids: Sequence[int], actor_user_id: int | None
    ) -> None:
        if self._classification_topics_repo is None:
            raise BadRequestError(
                "classificationTopics is not configured on this server",
                data={"spaceId": space_id},
            )
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        await self._classification_topics_repo.replace_topics_for_space(
            space_id=space_id, topic_ids=topic_ids
        )

    # ------------------------------------------------------------------
    # Basic queries
    # ------------------------------------------------------------------

    async def get_space(self, space_id: int) -> Space | None:
        return await self._repo.get_by_id(space_id)

    async def exists_by_name(self, name: str) -> bool:
        return await self._repo.exists_by_name(name)

    async def list_spaces(
        self, *, limit: int, offset: int = 0, member_user_id: int
    ) -> Sequence[Space]:
        return await self._repo.list_spaces(
            limit=limit, offset=offset, member_user_id=member_user_id
        )

    async def count_spaces(self, *, member_user_id: int) -> int:
        return await self._repo.count_spaces(member_user_id=member_user_id)

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

    async def get_category_detail(
        self, *, space_id: int, category_id: int
    ) -> SpaceCategory:
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
        visible_task_limit: int | None = None,
    ) -> Space:
        self._validate_strings(name=name)
        if not isinstance(intro, str) or not isinstance(description, str):
            raise BadRequestError("intro and description must be strings")
        normalized_announcements = self._normalize_json_list(announcements)
        normalized_templates = self._normalize_json_list(task_templates)
        self._validate_visible_task_limit(visible_task_limit)

        space = await self._repo.create_space(
            name=name.strip(),
            intro=intro.strip(),
            description=description.strip(),
            avatar_id=avatar_id,
            enable_rank=enable_rank,
            announcements=normalized_announcements,
            task_templates=normalized_templates,
            visible_task_limit=visible_task_limit,
        )

        # Default category "General". It declares the course 壳: a 题目板 is a
        # course now, so a new one opens as the course template rather than a
        # blank board. The 壳 is a DEFAULT on the one protocol chain — a 题目
        # may replace it and a project's own settings outrank both — and the
        # name comes from the catalog so no 壳 is named twice.
        default_category = await self._category_repo.create_category(
            space_id=space.id,
            name="General",
            description="Auto generated default category",
            display_order=0,
            shell=DEFAULT_CATEGORY_SHELL_NAME,
        )
        space.default_category_id = default_category.id
        await self._repo.save(space)

        if self._admin_repo is not None:
            await self._admin_repo.add_admin(
                space_id=space.id,
                user_id=owner_id,
                role=SpaceAdminRole.OWNER,
            )

        # Every 题目版 is created holding a code: membership is the only way
        # in, so a board whose creator has no code to hand out is a board
        # nobody can ever reach. Minting it here saves that first step.
        if self._invite_code_repo is not None:
            await self._invite_code_repo.create_code(
                space_id=space.id,
                code=await self._generate_invite_code(),
                max_uses=DEFAULT_SPACE_INVITE_CODE_MAX_USES,
                expires_at=None,
                created_by=owner_id,
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
        visible_task_limit: int | None = None,
        set_visible_task_limit: bool = False,
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
        if set_visible_task_limit:
            self._validate_visible_task_limit(visible_task_limit)
            space.visible_task_limit = visible_task_limit
        if default_category_id is not None:
            category = await self._category_repo.get_by_id(default_category_id)
            if category is None or category.space_id != space_id:
                raise NotFoundError(
                    "Space category not found",
                    data={"spaceId": space_id, "categoryId": default_category_id},
                )
            space.default_category_id = default_category_id

        space.updated_at = datetime.now(UTC)
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
            description=description.strip()
            if isinstance(description, str)
            else description,
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
        teaching: dict | None = None,
    ) -> SpaceCategory:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        category = await self._get_category(space_id, category_id)

        if name is not None:
            if not name.strip():
                raise BadRequestError("Category name cannot be empty")
            if (
                name.strip() != category.name
                and await self._category_repo.exists_unarchived_name(
                    space_id, name.strip()
                )
            ):
                raise BadRequestError("Category name already exists")
            category.name = name.strip()
        if description is not None:
            category.description = description.strip()
        if display_order is not None:
            category.display_order = display_order
        if archived is not None:
            category.archived_at = datetime.now(UTC) if archived else None
        if teaching is not None:
            # Whole-key replacement, same as the 赛题 override: what a teacher
            # saved is what is in force. `None` means the caller did not touch
            # it, so a rename does not silently wipe the 教学安排.
            category.teaching = teaching

        category.updated_at = datetime.now(UTC)
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

        category.deleted_at = datetime.now(UTC)
        category.updated_at = datetime.now(UTC)
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
        category.archived_at = datetime.now(UTC) if archived else None
        category.updated_at = datetime.now(UTC)
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
            if (
                role is SpaceAdminRole.OWNER
                and existing.role != SpaceAdminRole.OWNER.value
            ):
                await self._promote_admin_to_owner(space_id, existing)
                return
            raise BadRequestError("User already has a space role")
        if role is SpaceAdminRole.OWNER:
            current_owner = await self._admin_repo.get_owner(space_id)
            if current_owner is not None:
                current_owner.role = SpaceAdminRole.ADMIN.value
                current_owner.updated_at = datetime.now(UTC)
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
            raise BadRequestError(
                "Cannot remove space owner. Transfer ownership instead."
            )
        await self._admin_repo.remove_admin(relation)

    async def update_admin_role(
        self,
        *,
        space_id: int,
        target_user_id: int,
        new_role: SpaceAdminRole,
        actor_user_id: int | None,
    ) -> None:
        """Update an existing admin's role (e.g. ADMIN <-> OWNER)."""
        await self._ensure_admin(space_id, actor_user_id, allow_admin=False)
        if self._admin_repo is None:
            raise BadRequestError("Space admin repository unavailable")
        relation = await self._admin_repo.get_relation(space_id, target_user_id)
        if relation is None:
            raise NotFoundError("Space admin relation not found")
        if relation.role == new_role.value:
            return  # already the desired role
        if new_role is SpaceAdminRole.OWNER:
            await self._promote_admin_to_owner(space_id, relation)
        else:
            if relation.role == SpaceAdminRole.OWNER.value:
                raise BadRequestError(
                    "Cannot demote owner directly. Transfer ownership to another admin first."  # noqa: E501
                )
            relation.role = new_role.value
            relation.updated_at = datetime.now(UTC)
            await self._admin_repo.save(relation)

    async def delete_space(self, *, space_id: int, actor_user_id: int | None) -> None:
        space = await self._get_space_or_error(space_id)
        await self._ensure_admin(space_id, actor_user_id, allow_admin=False)
        space.deleted_at = datetime.now(UTC)
        space.updated_at = datetime.now(UTC)
        await self._repo.save(space)

    # ------------------------------------------------------------------
    # Membership and invite codes
    # ------------------------------------------------------------------

    async def list_members(self, space_id: int) -> Sequence[SpaceMember]:
        if self._member_repo is None:
            return []
        return await self._member_repo.list_members(space_id)

    async def join_space(self, *, code: str, user_id: int) -> Space:
        """Redeem a space invite code, becoming a member.

        The code is the ordinary way in: a 题目版 carries one from the moment
        it is created (see ``create_space``), and the creator hands it to
        whoever should be able to see the board.
        """
        invite_repo = self._require_invite_code_repo()
        member_repo = self._require_member_repo()

        invite = await invite_repo.get_by_code(code)
        if invite is None:
            raise NotFoundError("Invite code not found", data={"type": "inviteCode"})

        space = await self._repo.get_by_id(invite.space_id)
        if space is None:
            raise NotFoundError.for_resource("space", invite.space_id)

        if invite.expires_at is not None and invite.expires_at <= datetime.now(UTC):
            raise BadRequestError(
                "Invite code expired", data={"type": "inviteCode", "id": invite.id}
            )

        # Already in — redeeming again is a no-op rather than burning a use
        # or failing, so a double-tap on「加入」is not an error.
        if await member_repo.get_member(space.id, user_id) is not None:
            return space
        if self._admin_repo is not None:
            if await self._admin_repo.get_relation(space.id, user_id) is not None:
                return space

        # Membership first, use second, and the order is the point: the
        # membership write is idempotent and atomic (uq_space_member_active,
        # see `SpaceMemberRepository.add_member`), so its answer to "did I get
        # in" is the only one that two concurrent redemptions cannot both
        # claim. Consuming the use first, as this used to, spends one for the
        # loser of a double-tap: both read "not a member", both spend.
        _, created = await member_repo.add_member(space_id=space.id, user_id=user_id)
        if not created:
            # A concurrent redeem already let them in. No use spent, no error.
            return space

        if not await invite_repo.consume_use(invite.id):
            # Out of uses after all — or the code expired in the window
            # between the check above and the UPDATE that spends one, which
            # `consume_use` refuses for the same reason. Those are two
            # different things to be told, so ask which one this is instead of
            # calling it exhausted either way.
            #
            # Raising rolls the whole request back, the membership row
            # included, so the two writes stay one decision rather than a
            # membership with nothing behind it.
            current = await invite_repo.get_by_code(code)
            if (
                current is not None
                and current.expires_at is not None
                and current.expires_at <= datetime.now(UTC)
            ):
                raise BadRequestError(
                    "Invite code expired",
                    data={"type": "inviteCode", "id": invite.id},
                )
            raise BadRequestError(
                "Invite code exhausted", data={"type": "inviteCode", "id": invite.id}
            )
        return space

    async def leave_space(self, *, space_id: int, user_id: int) -> None:
        """Stop being a member. Nothing else the person owns is touched."""
        member_repo = self._require_member_repo()
        await self._get_space_or_error(space_id)

        # Adminship is granted and revoked by the creator, so it is not
        # something you put down on your way out; doing it here would be a
        # second, quieter way to lose the role than /managers.
        if self._admin_repo is not None:
            relation = await self._admin_repo.get_relation(space_id, user_id)
            if relation is not None:
                if relation.role == SpaceAdminRole.OWNER.value:
                    raise ForbiddenError(
                        "The space owner cannot leave. Transfer ownership or "
                        "delete the space instead."
                    )
                raise ForbiddenError(
                    "Space admins cannot leave. Ask the owner to revoke the role first."
                )

        member = await member_repo.get_member(space_id, user_id)
        if member is None:
            raise NotFoundError("You are not a member of this space")
        await member_repo.remove_member(member)

    async def add_member(
        self,
        *,
        space_id: int,
        target_user_id: int,
        actor_user_id: int | None,
    ) -> SpaceMember:
        """Put someone in the space directly.

        The owner's way to bring someone in without handing out a code —
        useful when the code has been spent or when only one person should
        have it.
        """
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        await self._get_space_or_error(space_id)
        member_repo = self._require_member_repo()

        existing = await member_repo.get_member(space_id, target_user_id)
        if existing is not None:
            return existing
        # Neither answer is interesting here — an admin put them in directly,
        # so there is no use to spend and nothing else that depends on whether
        # this call or a concurrent one wrote the row.
        member, _ = await member_repo.add_member(
            space_id=space_id, user_id=target_user_id
        )
        return member

    async def remove_member(
        self,
        *,
        space_id: int,
        target_user_id: int,
        actor_user_id: int | None,
    ) -> None:
        """Drop someone from the space. Owner and admins alike may do this.

        Removal only decides who the space is visible to. Their tasks,
        submissions and projects are not this space's to delete — the same
        reason leaving keeps them and deleting a space keeps them.
        """
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        member_repo = self._require_member_repo()

        if self._admin_repo is not None:
            relation = await self._admin_repo.get_relation(space_id, target_user_id)
            if relation is not None:
                raise BadRequestError(
                    "That user is an admin of this space. Revoke the role "
                    "through the managers endpoint instead.",
                    data={"spaceId": space_id, "userId": target_user_id},
                )

        member = await member_repo.get_member(space_id, target_user_id)
        if member is None:
            raise NotFoundError(
                "Space member not found",
                data={"spaceId": space_id, "userId": target_user_id},
            )
        await member_repo.remove_member(member)

    async def list_invite_codes(
        self, *, space_id: int, actor_user_id: int | None
    ) -> Sequence[SpaceInviteCode]:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        return await self._require_invite_code_repo().list_codes_for_space(space_id)

    async def create_invite_code(
        self,
        *,
        space_id: int,
        actor_user_id: int | None,
        max_uses: int | None = None,
        expires_at: datetime | None = None,
    ) -> SpaceInviteCode:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        await self._get_space_or_error(space_id)

        uses = DEFAULT_SPACE_INVITE_CODE_MAX_USES if max_uses is None else max_uses
        if isinstance(uses, bool) or not isinstance(uses, int) or uses < 1:
            raise BadRequestError("maxUses must be a positive integer")

        return await self._require_invite_code_repo().create_code(
            space_id=space_id,
            code=await self._generate_invite_code(),
            max_uses=uses,
            expires_at=expires_at,
            created_by=actor_user_id,
        )

    async def _generate_invite_code(self) -> str:
        invite_repo = self._require_invite_code_repo()
        for _ in range(10):
            candidate = "".join(
                secrets.choice(_INVITE_CODE_ALPHABET)
                for _ in range(_INVITE_CODE_LENGTH)
            )
            if not await invite_repo.code_exists(candidate):
                return candidate
        raise BadRequestError("Could not allocate an invite code, please retry")

    # ------------------------------------------------------------------
    # Domain groups
    # ------------------------------------------------------------------

    async def list_domain_groups(
        self, *, space_id: int, actor_user_id: int | None
    ) -> list[tuple[SpaceDomainGroup, list[str]]]:
        # Listing is open to all authenticated users so they can select
        # domain groups when publishing/editing tasks.
        group_repo = self._require_domain_group_repo()
        domain_repo = self._require_domain_group_domain_repo()

        groups = await group_repo.list_groups(space_id)
        domain_map = await domain_repo.list_domains_for_groups([g.id for g in groups])
        return [(group, domain_map.get(int(group.id), [])) for group in groups]

    async def create_domain_group(
        self,
        *,
        space_id: int,
        name: str,
        description: str | None,
        domains: list[str],
        actor_user_id: int | None,
    ) -> tuple[SpaceDomainGroup, list[str]]:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        group_repo = self._require_domain_group_repo()
        domain_repo = self._require_domain_group_domain_repo()

        if not name.strip():
            raise BadRequestError("Domain group name cannot be empty")
        normalized_domains = self._normalize_domains(domains)
        if not normalized_domains:
            raise BadRequestError("Domain list cannot be empty")
        if await group_repo.exists_name(space_id=space_id, name=name.strip()):
            raise BadRequestError("Domain group name already exists")

        group = await group_repo.create_group(
            space_id=space_id,
            name=name.strip(),
            description=description.strip()
            if isinstance(description, str)
            else description,
        )
        await domain_repo.replace_domains(group_id=group.id, domains=normalized_domains)
        return group, normalized_domains

    async def update_domain_group(
        self,
        *,
        space_id: int,
        group_id: int,
        name: str | None,
        description: str | None,
        domains: list[str] | None,
        actor_user_id: int | None,
    ) -> tuple[SpaceDomainGroup, list[str]]:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        group_repo = self._require_domain_group_repo()
        domain_repo = self._require_domain_group_domain_repo()

        group = await self._get_domain_group(space_id=space_id, group_id=group_id)

        if name is not None:
            if not name.strip():
                raise BadRequestError("Domain group name cannot be empty")
            if name.strip() != group.name and await group_repo.exists_name(
                space_id=space_id, name=name.strip()
            ):
                raise BadRequestError("Domain group name already exists")
            group.name = name.strip()

        if description is not None:
            group.description = description.strip()

        if domains is not None:
            normalized_domains = self._normalize_domains(domains)
            if not normalized_domains:
                raise BadRequestError("Domain list cannot be empty")
            await domain_repo.replace_domains(
                group_id=group.id, domains=normalized_domains
            )
        else:
            normalized_domains = await domain_repo.list_domains_for_group(group.id)

        group.updated_at = datetime.now(UTC)
        group = await group_repo.save(group)
        return group, normalized_domains

    async def delete_domain_group(
        self,
        *,
        space_id: int,
        group_id: int,
        actor_user_id: int | None,
    ) -> None:
        await self._ensure_admin(space_id, actor_user_id, allow_admin=True)
        group_repo = self._require_domain_group_repo()
        domain_repo = self._require_domain_group_domain_repo()

        group = await self._get_domain_group(space_id=space_id, group_id=group_id)
        group.deleted_at = datetime.now(UTC)
        group.updated_at = datetime.now(UTC)
        await group_repo.save(group)
        await domain_repo.soft_delete_by_group(group_id=group.id)

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
            raise NotFoundError(
                "Resource space not found", data={"type": "space", "id": space_id}
            )
        return space

    async def _promote_admin_to_owner(
        self,
        space_id: int,
        relation: SpaceAdminRelation,
    ) -> None:
        if self._admin_repo is None:
            raise BadRequestError("Admin operations not available")
        current_owner = await self._admin_repo.get_owner(space_id)
        if current_owner is not None:
            current_owner.role = SpaceAdminRole.ADMIN.value
            current_owner.updated_at = datetime.now(UTC)
            await self._admin_repo.save(current_owner)
        relation.role = SpaceAdminRole.OWNER.value
        relation.updated_at = datetime.now(UTC)
        await self._admin_repo.save(relation)

    async def _get_category(self, space_id: int, category_id: int) -> SpaceCategory:
        category = await self._category_repo.get_by_id(category_id)
        if category is None or category.space_id != space_id:
            raise NotFoundError(
                "Space category not found",
                data={"spaceId": space_id, "categoryId": category_id},
            )
        return category

    async def _get_domain_group(
        self, *, space_id: int, group_id: int
    ) -> SpaceDomainGroup:
        group_repo = self._require_domain_group_repo()
        group = await group_repo.get_by_id(space_id=space_id, group_id=group_id)
        if group is None:
            raise NotFoundError(
                "Space domain group not found",
                data={"spaceId": space_id, "groupId": group_id},
            )
        return group

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

    @staticmethod
    def _validate_visible_task_limit(value: int | None) -> None:
        if value is None:
            return
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BadRequestError(
                "visibleTaskLimit must be null or a non-negative integer"
            )

    def _require_member_repo(self) -> SpaceMemberRepository:
        if self._member_repo is None:
            raise BadRequestError("Space membership is not configured on this server")
        return self._member_repo

    def _require_invite_code_repo(self) -> SpaceInviteCodeRepository:
        if self._invite_code_repo is None:
            raise BadRequestError(
                "Space invite codes are not configured on this server"
            )
        return self._invite_code_repo

    def _require_domain_group_repo(self) -> SpaceDomainGroupRepository:
        if self._domain_group_repo is None:
            raise BadRequestError("Domain group repository unavailable")
        return self._domain_group_repo

    def _require_domain_group_domain_repo(self) -> SpaceDomainGroupDomainRepository:
        if self._domain_group_domain_repo is None:
            raise BadRequestError("Domain group repository unavailable")
        return self._domain_group_domain_repo

    @staticmethod
    def _normalize_domains(domains: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in domains:
            if not isinstance(raw, str):
                raise BadRequestError("Invalid domain entry")
            value = raw.strip().lower()
            if not value:
                continue
            if "@" in value or " " in value or "." not in value:
                raise BadRequestError(f"Invalid domain: {raw}")
            if value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized
