import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.space.material_service import member_readable_material_ids
from app.domain.space.member_repositories import APPROVED, REJECTED
from app.domain.space.models import (
    Space,
    SpaceAdminRelation,
    SpaceAdminRole,
    SpaceCategory,
    SpaceDomainGroup,
    SpaceJoinRequest,
    SpaceMember,
)
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceClassificationTopicsRepository,
    SpaceDomainGroupDomainRepository,
    SpaceDomainGroupRepository,
    SpaceJoinRepository,
    SpaceMemberRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.tag.models import Tag

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.materials.services import MaterialService
    from app.domain.task.repositories import TaskRepository


def _error_data_id(exc: NotFoundError) -> object | None:
    """The id a not-found error named, so a form-field error can carry it on."""
    data = exc.data
    return data.get("id") if isinstance(data, dict) else None


#: Refuses the team documents a 指导 names unless each exists and the writer may
#: read it (`api.doc_access.ensure_team_documents`): `(document_ids, user_id)`.
DocumentCheck = Callable[[list[str], int], Awaitable[None]]


async def ensure_teaching_references(
    *,
    document_check: "DocumentCheck | None",
    material_service: "MaterialService | None",
    session: "AsyncSession | None",
    teaching: dict,
    actor_user_id: int | None,
) -> None:
    """Every id a 给 AI 队友的指导 names must exist — and, for a document, be readable.

    The read side (`Teaching.from_json`, `teaching.for_project`) drops what
    it cannot resolve, because a typo there would take down every 赛题 under the
    board. Here a person is looking at the form and can be told which field is
    wrong, so a bad reference is refused with the field's name instead of being
    dropped.

    Module-level rather than a method because the SAME check guards all three
    places a 指导 is written — the 项目集 form, the 空间默认 (#944), and a
    single 题目 — and the three must agree on what counts as a valid reference.
    Task-level writes reach it through the api layer (which may import either
    domain) rather than through one domain importing the other, so this stays
    the one implementation without adding a domain edge.

    A document must be a team's own that the teacher could already open — a
    member of its team (#3774); a course's agents read it, nobody else's. A
    课件 has no owning team to check, but it does have a
    visibility tier: a plain `Material` is open to any signed-in reader, while
    one placed in a board's「仅管理员」tier is not
    (`SpaceMaterialService.may_read_outside_space`). So a `material_ids` entry
    must both exist and be one an ordinary member could read; naming an
    admins-only 课件 is refused by the field's name, rather than accepted here
    and silently dropped by `for_project` later.
    """
    document_ids = [str(i) for i in teaching.get("document_ids") or []]
    if document_ids and document_check is not None:
        if actor_user_id is None:
            raise ForbiddenError("Authentication required")
        try:
            await document_check(document_ids, actor_user_id)
        except NotFoundError as exc:
            raise BadRequestError(
                "teaching.documentIds names a document that does not exist",
                data={"field": "documentIds", "id": _error_data_id(exc)},
            ) from exc

    material_ids = teaching.get("material_ids") or []
    if material_ids and material_service is not None:
        try:
            await material_service.ensure_exist(material_ids=material_ids)
        except NotFoundError as exc:
            raise BadRequestError(
                "teaching.materialIds names a material that does not exist",
                data={"field": "materialIds", "id": _error_data_id(exc)},
            ) from exc
        if session is not None:
            # The read side keeps a config safe when a 课件's tier is flipped
            # afterwards, but this is a form somebody is looking at: say which
            # field is wrong instead of quietly leaving a 课件 out this week.
            readable = await member_readable_material_ids(
                session, material_ids=material_ids
            )
            locked = [mid for mid in material_ids if mid not in readable]
            if locked:
                # i18n-exempt: an English message; 课件 is the UI label it names
                raise BadRequestError(
                    "teaching.materialIds names a 课件 that is only visible to a "
                    "board's managers",
                    data={"field": "materialIds", "id": locked[0]},
                )


@dataclass(frozen=True)
class SpaceLabel:
    """How a board is named where something happened on it."""

    name: str


class SpaceLabels:
    """Names boards for records of what happened on them.

    Deleted boards are named too: a board removed since is still where the
    thing happened.
    """

    def __init__(self, session: "AsyncSession") -> None:
        self._repo = SpaceRepository(session=session)

    async def describe(self, space_ids: Sequence[int]) -> dict[int, SpaceLabel]:
        """Each of these boards' labels, in one query; unknown ids are left out."""
        rows = await self._repo.names(space_ids=space_ids)
        return {space_id: SpaceLabel(name=name) for space_id, name in rows.items()}


#: The first category a new space gets, named in its creator's UI language
#: (``User.language``; unset reads as Chinese, as everywhere a person is
#: addressed). Its name is ordinary data the owner can rename afterwards.
DEFAULT_CATEGORY: Final[dict[str, tuple[str, str]]] = {
    "zh-CN": ("默认分类", "新建空间时自动创建的分类"),
    "en": ("General", "Created with the space"),
}


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
        join_repo: SpaceJoinRepository | None = None,
        document_check: "DocumentCheck | None" = None,
        material_service: "MaterialService | None" = None,
        session: "AsyncSession | None" = None,
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
        self._join_repo = join_repo
        self._document_check = document_check
        self._material_service = material_service
        self._session = session

    @classmethod
    def for_routes(
        cls,
        session: "AsyncSession",
        *,
        task_repo: "TaskRepository | None" = None,
        document_check: "DocumentCheck | None" = None,
        material_service: "MaterialService | None" = None,
    ) -> "SpaceService":
        """The board service the space routes hand out — wired here rather than in
        the route.

        Assembling it needs nine ``space.repositories``; a route that builds them
        itself reaches past this domain's service layer (architecture guard C2),
        so the wiring lives with the repositories it wires. ``task_repo`` /
        ``document_check`` / ``material_service`` are passed in by the caller
        because the write side of ``teaching``'s references reaches into those
        domains, and this module keeps those out of its runtime imports.
        """
        return cls(
            repo=SpaceRepository(session),
            category_repo=SpaceCategoryRepository(session),
            admin_repo=SpaceAdminRelationRepository(session),
            rank_repo=SpaceUserRankRepository(session),
            task_repo=task_repo,
            classification_topics_repo=SpaceClassificationTopicsRepository(session),
            domain_group_repo=SpaceDomainGroupRepository(session),
            domain_group_domain_repo=SpaceDomainGroupDomainRepository(session),
            member_repo=SpaceMemberRepository(session),
            join_repo=SpaceJoinRepository(session),
            document_check=document_check,
            material_service=material_service,
            session=session,
        )

    @classmethod
    def for_visibility(cls, session: "AsyncSession") -> "SpaceService":
        """Wired for the two plain reads a visibility gate needs — the board
        (``get_space``) and whether one person reads it (``is_reader``).

        ``require_reviewed_space`` runs on every board request, so this build
        stays small: the full constructor's other repositories are not needed to
        answer "does this board exist / is this person in it".
        """
        return cls(
            repo=SpaceRepository(session),
            category_repo=SpaceCategoryRepository(session),
        )

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
        self, *, space_id: int, topic_ids: Sequence[int]
    ) -> None:
        if self._classification_topics_repo is None:
            raise BadRequestError(
                "classificationTopics is not configured on this server",
                data={"spaceId": space_id},
            )
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

    async def is_reader(self, *, space_id: int, user_id: int) -> bool:
        return await self._repo.is_reader(space_id=space_id, user_id=user_id)

    @staticmethod
    def admin_role_name(role: int) -> str:
        """The name the API shows for an admin relation's stored role.

        Lives beside the enum so a route can label a row without importing
        ``SpaceAdminRole`` to read it off the model.
        """
        if role == SpaceAdminRole.OWNER.value:
            return "OWNER"
        return "ADMIN"

    @staticmethod
    def admin_role_from_name(name: str) -> SpaceAdminRole | None:
        """The stored role for a client-supplied role name (``OWNER``/``ADMIN``).

        ``None`` for anything else, so the caller keeps its own error message.
        Beside the enum so a route can turn the name into the value
        ``add_admin`` / ``update_admin_role`` expect without importing
        ``SpaceAdminRole`` itself.
        """
        return {
            "OWNER": SpaceAdminRole.OWNER,
            "ADMIN": SpaceAdminRole.ADMIN,
        }.get(name.upper())

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
        task_templates: list,
        visible_task_limit: int | None = None,
        language: str | None = None,
    ) -> Space:
        self._validate_strings(name=name)
        if not isinstance(intro, str) or not isinstance(description, str):
            raise BadRequestError("intro and description must be strings")
        normalized_templates = self._normalize_json_list(task_templates)
        self._validate_visible_task_limit(visible_task_limit)

        space = await self._repo.create_space(
            name=name.strip(),
            intro=intro.strip(),
            description=description.strip(),
            avatar_id=avatar_id,
            enable_rank=enable_rank,
            task_templates=normalized_templates,
            visible_task_limit=visible_task_limit,
        )

        # The default category. It declares no 壳, so projects under it run
        # the default one unless a 题目 or the project itself says otherwise.
        category_name, category_description = DEFAULT_CATEGORY.get(
            language or "zh-CN", DEFAULT_CATEGORY["zh-CN"]
        )
        default_category = await self._category_repo.create_category(
            space_id=space.id,
            name=category_name,
            description=category_description,
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
        task_templates: list | None = None,
        default_category_id: int | None = None,
        visible_task_limit: int | None = None,
        set_visible_task_limit: bool = False,
        teaching: dict | None = None,
    ) -> Space:
        space = await self._get_space_or_error(space_id)

        if teaching is not None:
            # Refused here, before a single field of the row is touched: a
            # rejected PATCH must leave the stored 指导 exactly as it was.
            await self._ensure_teaching_references(
                teaching=teaching, actor_user_id=actor_user_id
            )

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
        if teaching is not None:
            # Whole-key replacement, the same rule the 项目集 and 题目 levels
            # follow (#944): what the manager saved is what is in force. Absent
            # (``None``) means "did not touch it", so a rename through this
            # endpoint does not silently wipe the board-wide 指导.
            space.teaching = teaching
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
        default_role: str | None = None,
    ) -> SpaceCategory:
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
            # 项目集级默认队友 (#944)：空串在新行上没有要清的继承，落成「没说」。
            default_role=(default_role.strip() or None) if default_role else None,
        )

    async def update_category(
        self,
        *,
        space_id: int,
        category_id: int,
        name: str | None = None,
        description: str | None = None,
        display_order: int | None = None,
        archived: bool | None = None,
        default_role: str | None = None,
    ) -> SpaceCategory:
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
        if default_role is not None:
            # 项目集级默认队友 (#944) — declared once here, inherited by every
            # 题目 under it. Same whole-key rule: `None` = the caller did not
            # touch it; a blank string is a WRITE that clears it.
            category.default_role = default_role.strip() or None
        category.updated_at = datetime.now(UTC)
        return await self._category_repo.save(category)

    async def _ensure_teaching_references(
        self, *, teaching: dict, actor_user_id: int | None
    ) -> None:
        """The service's wiring of `ensure_teaching_references` — see it for the
        rule; this only hands in the lookups and the session the route builds."""
        await ensure_teaching_references(
            document_check=self._document_check,
            material_service=self._material_service,
            session=self._session,
            teaching=teaching,
            actor_user_id=actor_user_id,
        )

    async def delete_category(
        self,
        *,
        space_id: int,
        category_id: int,
    ) -> None:
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
    ) -> SpaceCategory:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
        """Update an existing admin's role (e.g. ADMIN <-> OWNER)."""
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
                    "Cannot demote owner directly. Transfer ownership to another admin first."
                )
            relation.role = new_role.value
            relation.updated_at = datetime.now(UTC)
            await self._admin_repo.save(relation)

    async def delete_space(self, *, space_id: int) -> None:
        space = await self._get_space_or_error(space_id)
        space.deleted_at = datetime.now(UTC)
        space.updated_at = datetime.now(UTC)
        await self._repo.save(space)

    # ------------------------------------------------------------------
    # Membership and the join link
    # ------------------------------------------------------------------

    async def list_members(self, space_id: int) -> Sequence[SpaceMember]:
        if self._member_repo is None:
            return []
        return await self._member_repo.list_members(space_id)

    async def join_link(
        self,
        *,
        space_id: int,
        reset: bool = False,
        approval: bool | None = None,
    ) -> Space:
        """The space's join link, minted on first ask; ``reset`` replaces it.

        Resetting makes the old link answer like a space that does not exist;
        whoever already joined through it stays a member.
        """
        space = await self._get_space_or_error(space_id)
        if reset or space.join_token is None:
            space.join_token = secrets.token_urlsafe(32)
        if approval is not None:
            space.join_approval = approval
        space.updated_at = datetime.now(UTC)
        await self._repo.save(space)
        return space

    async def space_for_join_token(self, token: str) -> Space:
        """The space a link names, for whoever holds it, or 404.

        A reset link, a deleted space and one the platform has not approved
        all answer the same way a space nobody may see does, so a link tells
        nobody more than holding a valid one would.
        """
        space = await self._require_join_repo().space_by_token(token)
        if space is None or space.review_status != "APPROVED":
            raise NotFoundError(say("spaceJoinLinkInvalid"))
        return space

    async def join_status(self, *, space_id: int, user_id: int) -> str:
        """``member`` (in it, or running it), ``pending`` (a request waits for
        approval) or ``none``."""
        if await self._require_member_repo().get_member(space_id, user_id):
            return "member"
        if self._admin_repo is not None and await self._admin_repo.get_relation(
            space_id, user_id
        ):
            return "member"
        if await self._require_join_repo().pending_for(space_id, user_id):
            return "pending"
        return "none"

    async def join_by_link(self, *, token: str, user_id: int) -> tuple[Space, str]:
        """Join the space the link names, or ask to — whichever its
        ``join_approval`` says. Returns the space and where the person now
        stands. Asking twice, or asking once already in, is not an error."""
        space = await self.space_for_join_token(token)
        status = await self.join_status(space_id=space.id, user_id=user_id)
        if status != "none":
            return space, status
        if space.join_approval:
            await self._require_join_repo().ask(space_id=space.id, user_id=user_id)
            return space, "pending"
        await self._require_member_repo().add_member(space_id=space.id, user_id=user_id)
        return space, "member"

    async def list_join_requests(self, *, space_id: int) -> Sequence[SpaceJoinRequest]:
        """The requests waiting on this space, oldest first."""
        await self._get_space_or_error(space_id)
        return await self._require_join_repo().list_pending(space_id)

    async def decide_join_request(
        self, *, space_id: int, request_id: int, approve: bool, actor_user_id: int
    ) -> None:
        """Approve (the requester becomes a member) or reject a waiting request."""
        await self._get_space_or_error(space_id)
        request = await self._require_join_repo().decide(
            space_id=space_id,
            request_id=request_id,
            status=APPROVED if approve else REJECTED,
            decided_by=actor_user_id,
        )
        if request is None:
            raise NotFoundError(
                say("spaceJoinRequestNotFound"),
                data={"spaceId": space_id, "requestId": request_id},
            )
        if approve:
            await self._require_member_repo().add_member(
                space_id=space_id, user_id=request.user_id
            )

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
    ) -> SpaceMember:
        """Put someone in the space directly.

        The owner's way to bring someone in without handing out the link —
        useful when only one person should get in.
        """
        await self._get_space_or_error(space_id)
        member_repo = self._require_member_repo()

        existing = await member_repo.get_member(space_id, target_user_id)
        if existing is not None:
            return existing
        # Neither answer is interesting here — an admin put them in directly,
        # and nothing depends on whether this call or a concurrent one wrote
        # the row.
        member, _ = await member_repo.add_member(
            space_id=space_id, user_id=target_user_id
        )
        return member

    async def remove_member(
        self,
        *,
        space_id: int,
        target_user_id: int,
    ) -> None:
        """Drop someone from the space. Owner and admins alike may do this.

        Removal only decides who the space is visible to. Their tasks,
        submissions and projects are not this space's to delete — the same
        reason leaving keeps them and deleting a space keeps them.
        """
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

    # ------------------------------------------------------------------
    # Domain groups
    # ------------------------------------------------------------------

    async def list_domain_groups(
        self, *, space_id: int
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
    ) -> tuple[SpaceDomainGroup, list[str]]:
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
    ) -> tuple[SpaceDomainGroup, list[str]]:
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
    ) -> None:
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

    def _require_join_repo(self) -> SpaceJoinRepository:
        if self._join_repo is None:
            raise BadRequestError("Space join links are not configured on this server")
        return self._join_repo

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
