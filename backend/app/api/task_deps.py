"""题目路由共用的服务装配：依赖提供者，以及「取不到就 404」那个读法。

从 ``app/api/routes/tasks/_common.py`` 原样搬来，逐行未改。

搬家的理由只有一条：这一套装配不只是题目路由在用。``spaces_analytics.py`` 要
``get_task_submission_service``（空间提交队列复用题目域的提交服务），
``task_inheritance.py`` 要 ``_require_task``（沿继承链取题的那几条路由要先确认题
存在）。这两处都在 ``routes/`` 包**外面**，于是 ``routes/spaces_analytics`` 和
``routes/task_inheritance`` 各欠了一条 route→route 的 import —— C4 冻结账本上正是
这两条（``app.api.routes.spaces_analytics -> app.api.routes.tasks._common``、
``app.api.routes.task_inheritance -> app.api.routes.tasks._common``）：一条把
``tasks`` 这个包钉在两个父路由底下，谁想动 ``_common`` 先动别处的路由。

判据不是「谁在 import」，是「**这道装配的是谁**」。这里每个名字返回的都是别的域的
服务（``TaskService``、``TeamService``、``TaskSubmissionReviewService``……），路由
只把会话递进来，装配本身跟「题目路由」这个身份无关，跨包的那两处借的是同一份装配。
所以它和 ``admin_deps``、``users_deps``、``space_deps`` 一样，落在 ``app/api/`` 下 ——
依赖模块在 ``routes/`` 之外，谁都可以依赖，route→route 那条边也就没有了。

留在 ``_common.py`` 里的是请求体模型和领域构造辅助（``_create_task_entity``）；本模块
只回答「一个题目路由的服务从哪来，带着哪些协作者」。
"""

from fastapi import Depends

from app.core.errors import NotFoundError
from app.core.storage import get_storage_backend
from app.db.session import get_db
from app.domain.attachment.services import AttachmentService
from app.domain.space.rank_service import SpaceRankService
from app.domain.space.repositories import (
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    TaskSubmissionReviewService,
    TaskSubmissionService,
)
from app.domain.team.repositories import TeamRepository
from app.domain.team.repositories import TeamRepository as _TeamRepo
from app.domain.team.services import TeamService
from app.domain.user.repositories import (
    UserRealNameRepository,
)


async def get_task_service(db=Depends(get_db)) -> TaskService:
    return TaskService.of(db)


async def get_task_membership_service(db=Depends(get_db)) -> TaskMembershipService:
    # 跨域的仓库（user/space/team）路由自己 new 再传进去；本域的成员仓库由 service
    # 自己在内部拼 —— 那几笔跨域豁免留着，题目域内的这一笔不再从路由伸进 repositories。
    return TaskMembershipService.of(
        db,
        realname_repo=UserRealNameRepository(session=db),
        space_repo=SpaceRepository(session=db),
        space_rank_repo=SpaceUserRankRepository(session=db),
        team_repo=_TeamRepo(session=db),
    )


async def get_task_submission_service(db=Depends(get_db)) -> TaskSubmissionService:
    return TaskSubmissionService.of(
        db,
        attachments=AttachmentService.from_session(
            session=db, storage=get_storage_backend()
        ),
    )


async def get_task_submission_review_service(
    db=Depends(get_db),
) -> TaskSubmissionReviewService:
    space_repo = SpaceRepository(session=db)
    rank_repo = SpaceUserRankRepository(session=db)
    rank_service = SpaceRankService(space_repo=space_repo, rank_repo=rank_repo)
    return TaskSubmissionReviewService.of(db, rank_service=rank_service)


async def get_team_service(db=Depends(get_db)) -> TeamService:
    repo = TeamRepository(session=db)
    return TeamService(repo)


def _attachment_service(db) -> AttachmentService:
    """人先传上来的文件那一层（``POST /attachments`` 用的同一个服务）：发题时选的
    材料先传成这里的附件，建题时再拷进题目资料库。

    ``from_session`` 而不是自己造它的 repository：这里在题目这一域的地盘上，
    摸附件那一域的 repository 正是 ``test_domain_import_guard.py`` 拦的那一条。
    """
    return AttachmentService.from_session(session=db, storage=get_storage_backend())


async def _require_task(db, task_id: int):
    task = await TaskService.of(db).get_task(task_id)
    if task is None:
        raise NotFoundError("Task not found")
    return task
