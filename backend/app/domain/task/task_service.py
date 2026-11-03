"""The task service.

Split out of ``app/domain/task/services.py`` (one service class) so that module
stays under its size cap; the class is re-exported from ``services``, so
``from app.domain.task.services import TaskService`` keeps resolving.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.tag.models import Tag
from app.domain.task import stages
from app.domain.task.models import (
    Task,
    TaskRubricItem,
    TaskStage,
    TaskSubmissionSchemaEntry,
)
from app.domain.task.repositories import (
    TaskAccessDomainRepository,
    TaskRepository,
    TaskRubricRepository,
    TaskSubmissionSchemaRepository,
    TopicRepository,
)
from app.domain.task.rubric import RubricLine


class TaskService:
    def __init__(
        self, repo: TaskRepository, session: AsyncSession | None = None
    ) -> None:
        self._repo = repo
        # 只有 ``of`` 造出来时才带 session。单元测试直接 ``TaskService(repo)``
        # 用 AsyncMock 的仓库构造，那些用例不碰下面这几个写方法。
        self._session = session

    async def get_task(self, task_id: int) -> Task | None:
        return await self._repo.get_by_id(task_id)

    async def get_tasks_by_ids(self, task_ids: Sequence[int]) -> dict[int, Task]:
        return await self._repo.get_by_ids(task_ids)

    @classmethod
    def of(cls, session: AsyncSession) -> "TaskService":
        return cls(TaskRepository(session=session), session=session)

    async def search_in_space(
        self, space_id: int, words: Sequence[str], *, viewer_id: int, limit: int
    ) -> Sequence[Task]:
        """The space's 题目 matching every word, as ``viewer_id`` may list them."""
        return await self._repo.search_in_space(
            space_id, words, viewer_id=viewer_id, limit=limit
        )

    async def list_joined(self, user_id: int, *, limit: int = 30) -> Sequence[Task]:
        """The tasks ``user_id`` takes part in, across every space."""
        return await self._repo.list_joined(user_id, limit=limit)

    async def enumerate_tasks(
        self,
        *,
        space_id: int,
        category_id: int | None = None,
        approved: int | None = None,
        owner_id: int | None = None,
        keywords: str | None = None,
        topics: Sequence[int] | None = None,
        joined: bool | None = None,
        current_user_id: int | None = None,
        viewer_user_id: int | None = None,
        viewer_email_domain: str | None = None,
        viewer_is_space_admin: bool = False,
        apply_space_task_visibility: bool = False,
        visible_task_limit: int | None = None,
        lifecycle: str | None = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "publishedAt",
        sort_order: str = "desc",
    ) -> Sequence[Task]:
        return await self._repo.list_tasks(
            space_id=space_id,
            category_id=category_id,
            approved=approved,
            owner_id=owner_id,
            keywords=keywords,
            topics=topics,
            joined=joined,
            current_user_id=current_user_id,
            viewer_user_id=viewer_user_id,
            viewer_email_domain=viewer_email_domain,
            viewer_is_space_admin=viewer_is_space_admin,
            apply_space_task_visibility=apply_space_task_visibility,
            visible_task_limit=visible_task_limit,
            lifecycle=lifecycle,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def count_tasks(
        self,
        *,
        space_id: int,
        category_id: int | None = None,
        approved: int | None = None,
        owner_id: int | None = None,
        keywords: str | None = None,
        topics: Sequence[int] | None = None,
        joined: bool | None = None,
        current_user_id: int | None = None,
        viewer_user_id: int | None = None,
        viewer_email_domain: str | None = None,
        viewer_is_space_admin: bool = False,
        apply_space_task_visibility: bool = False,
        visible_task_limit: int | None = None,
        lifecycle: str | None = None,
    ) -> int:
        return await self._repo.count_tasks(
            space_id=space_id,
            category_id=category_id,
            approved=approved,
            owner_id=owner_id,
            keywords=keywords,
            topics=topics,
            joined=joined,
            current_user_id=current_user_id,
            viewer_user_id=viewer_user_id,
            viewer_email_domain=viewer_email_domain,
            viewer_is_space_admin=viewer_is_space_admin,
            apply_space_task_visibility=apply_space_task_visibility,
            visible_task_limit=visible_task_limit,
            lifecycle=lifecycle,
        )

    # --- 给路由用的薄写方法 -------------------------------------------------
    # 这些是同一域内的读写，路由（``tasks/_common.py``、``tasks/lifecycle.py``）
    # 原来自己 new 出 TaskRepository 再调；收进 service 后路由不再碰这一域的
    # models/repositories。语义与 repository 上同名方法逐字一致。

    def _require_session(self) -> AsyncSession:
        assert self._session is not None, "TaskService.of() sets the session"
        return self._session

    async def has_prior_pending_task_for_creator(self, task: Task) -> bool:
        return await self._repo.has_prior_pending_task_for_creator(task)

    async def save(self, task: Task) -> Task:
        return await self._repo.save(task)

    async def create_task(
        self,
        *,
        name: str,
        intro: str,
        description: str,
        creator_id: int,
        space_id: int,
        category_id: int,
        submitter_type: int,
        deadline: datetime | None,
        registration_start_at: datetime | None,
        participant_limit: int | None,
        default_deadline: int,
        resubmittable: bool,
        editable: bool,
        rank: int | None,
        require_real_name: bool,
        min_team_size: int | None,
        max_team_size: int | None,
        team_locking_policy: str,
        access_control_enabled: bool = False,
        start_mode: str = "DIRECT",
    ) -> Task:
        return await self._repo.create_task(
            name=name,
            intro=intro,
            description=description,
            creator_id=creator_id,
            space_id=space_id,
            category_id=category_id,
            submitter_type=submitter_type,
            deadline=deadline,
            registration_start_at=registration_start_at,
            participant_limit=participant_limit,
            default_deadline=default_deadline,
            resubmittable=resubmittable,
            editable=editable,
            rank=rank,
            require_real_name=require_real_name,
            min_team_size=min_team_size,
            max_team_size=max_team_size,
            team_locking_policy=team_locking_policy,
            access_control_enabled=access_control_enabled,
            start_mode=start_mode,
        )

    async def replace_topic_relations(
        self, *, task_id: int, topic_ids: Sequence[int]
    ) -> None:
        await self._repo.replace_topic_relations(task_id=task_id, topic_ids=topic_ids)

    async def add_topic_relations(
        self, *, task_id: int, topic_ids: Sequence[int]
    ) -> None:
        await self._repo.add_topic_relations(task_id=task_id, topic_ids=topic_ids)

    async def list_topics(self, task_id: int) -> Sequence[Tag]:
        return await TopicRepository(session=self._require_session()).list_by_task_id(
            task_id
        )

    async def list_submission_schema(
        self, task_id: int
    ) -> Sequence[TaskSubmissionSchemaEntry]:
        return await TaskSubmissionSchemaRepository(
            session=self._require_session()
        ).list_by_task_id(task_id)

    async def replace_submission_schema(
        self, task_id: int, entries: list[dict]
    ) -> None:
        await TaskSubmissionSchemaRepository(
            session=self._require_session()
        ).replace_schema(task_id, entries)

    async def list_submission_schemas(
        self, task_ids: Sequence[int]
    ) -> dict[int, list[TaskSubmissionSchemaEntry]]:
        return await TaskSubmissionSchemaRepository(
            session=self._require_session()
        ).list_by_task_ids(task_ids)

    async def list_rubric(self, task_id: int) -> list[TaskRubricItem]:
        return await TaskRubricRepository(
            session=self._require_session()
        ).list_by_task_id(task_id)

    async def replace_rubric(self, task_id: int, lines: Sequence[RubricLine]) -> None:
        await TaskRubricRepository(session=self._require_session()).replace(
            task_id, lines
        )

    async def list_stages(self, task_id: int) -> list[TaskStage]:
        return await stages.list_stages(self._require_session(), task_id)

    async def replace_stages(
        self, task_id: int, lines: Sequence[stages.StageLine]
    ) -> None:
        await stages.replace_stages(self._require_session(), task_id, lines)

    async def stage_keys(self, task_id: int) -> list[str]:
        return await stages.stage_keys(self._require_session(), task_id)

    async def replace_access_domains(
        self, *, task_id: int, domains: Sequence[str]
    ) -> None:
        await TaskAccessDomainRepository(
            session=self._require_session()
        ).replace_domains(task_id=task_id, domains=domains)
