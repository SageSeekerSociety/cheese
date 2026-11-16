"""批改一版提交：多位老师各判一份，判通过就推进领取的完成状态、按题目排名发放空间等级。

从 ``app.domain.task.services`` 搬来 —— 那个模块超了文件行数上限。

自动行的合成（``auto_scores``，方案 §评审那一行）：规则在
``app.domain.task.grading``/``usage_score``，由路由在调这里之前算好、按本来的形状
存进来；这里只管存和读。
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.space.rank_service import SpaceRankService
from app.domain.task.repositories import (
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionRepository,
    TaskSubmissionReviewRepository,
)
from app.domain.task.submission_state import refresh_completion_status_for_submission


class TaskSubmissionReviewService:
    """Simplified Python port of TaskSubmissionReviewService with rank hooks."""

    def __init__(
        self,
        review_repo: TaskSubmissionReviewRepository,
        submission_repo: TaskSubmissionRepository | None = None,
        membership_repo: TaskMembershipRepository | None = None,
        task_repo: TaskRepository | None = None,
        rank_service: SpaceRankService | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._review_repo = review_repo
        self._submission_repo = submission_repo
        self._membership_repo = membership_repo
        self._task_repo = task_repo
        self._rank_service = rank_service
        # 同 TaskSubmissionService：推进完成状态要在这个事务里读提交与评审、写领取
        # 行。路由工厂永远传 session；为 None 只在单元测试那种全 AsyncMock 的构造里
        # 出现，那里没有库可写。
        self._session = session

    @classmethod
    def of(
        cls,
        session: AsyncSession,
        *,
        rank_service: SpaceRankService | None = None,
    ) -> "TaskSubmissionReviewService":
        """路由工厂用的构造：同域的四个仓库在这里建，排名服务由调用方传进来。"""
        return cls(
            review_repo=TaskSubmissionReviewRepository(session=session),
            submission_repo=TaskSubmissionRepository(session=session),
            membership_repo=TaskMembershipRepository(session=session),
            task_repo=TaskRepository(session=session),
            rank_service=rank_service,
            session=session,
        )

    async def get_review_dto(
        self, submission_id: int, reviewer_id: int | None = None
    ) -> dict:
        """这一版的评审 DTO。给了 `reviewer_id` 就是那个人的那一条，否则是最早的
        一条 —— 一版可以有多位老师各判一份。"""
        review = await self._review_repo.get_by_submission_id(
            submission_id, reviewer_id
        )
        if review is None:
            return {"reviewed": False}
        detail = {
            "accepted": review.accepted,
            "score": review.score,
            "comment": review.comment,
        }
        auto_scores = getattr(review, "auto_scores", None)
        if auto_scores:
            detail["autoScores"] = auto_scores
        return {"reviewed": True, "detail": detail}

    async def create_review(
        self,
        *,
        submission_id: int,
        reviewer_id: int,
        accepted: bool,
        score: int,
        comment: str,
        auto_scores: dict | None = None,
    ) -> dict:
        review = await self._review_repo.create_review(
            submission_id=submission_id,
            reviewer_id=reviewer_id,
            accepted=accepted,
            score=score,
            comment=comment,
        )
        if auto_scores is not None:
            review.auto_scores = auto_scores
            await self._review_repo.save(review)
        has_upgraded = await self._maybe_award_rank(
            submission_id=submission_id,
            previous_accepted=None,
            new_accepted=accepted,
        )
        # 判通过就该是 SUCCESS，驳回就该是「可重交」—— 这一步是这条轴真正的推进者。
        await self._refresh_membership_status(submission_id)
        dto = await self.get_review_dto(review.submission_id, reviewer_id)
        dto["hasUpgradedParticipantRank"] = has_upgraded
        return dto

    async def patch_review(
        self,
        *,
        submission_id: int,
        reviewer_id: int | None = None,
        accepted: bool | None = None,
        score: int | None = None,
        comment: str | None = None,
        auto_scores: dict | None = None,
        rescored: bool = False,
    ) -> dict:
        """改一条评审。给了 `reviewer_id` 就改那个人的那份，没给就是最早的一份
        （单评审时两者是同一份）。

        ponytail: 一版多人时「最早那一份」是个封顶 —— 真要按人改，路径上要带上
        谁的那一份；今天调用方还没带，所以留这个默认。
        """
        review = await self._review_repo.get_by_submission_id(
            submission_id, reviewer_id
        )
        if review is None:
            raise NotFoundError.for_resource("review", submission_id)
        previous_accepted = review.accepted
        if accepted is not None:
            review.accepted = accepted
        if score is not None:
            review.score = score
        if rescored:
            # 分是重新合成的（``auto_score.compose_review``）：怎么来的跟着换，
            # 不再有自动行时清掉。
            review.auto_scores = auto_scores
        if comment is not None:
            review.comment = comment
        review.updated_at = datetime.now(UTC)
        await self._review_repo.save(review)
        has_upgraded = await self._maybe_award_rank(
            submission_id=submission_id,
            previous_accepted=previous_accepted,
            new_accepted=review.accepted,
        )
        # 改判也要跟上：通过改成驳回，这条领取就从 SUCCESS 退回「可重交」。
        await self._refresh_membership_status(submission_id)
        dto = await self.get_review_dto(submission_id, reviewer_id)
        dto["hasUpgradedParticipantRank"] = has_upgraded
        return dto

    async def delete_review(
        self, *, submission_id: int, reviewer_id: int | None = None
    ) -> None:
        review = await self._review_repo.get_by_submission_id(
            submission_id, reviewer_id
        )
        if review is None:
            return
        await self._review_repo.soft_delete(review)
        # 撤销评审是「把话收回去」：没有评审了，这一版就回到队列里，状态必须跟着退回
        # 去，不能留在 SUCCESS 上。
        await self._refresh_membership_status(submission_id)

    async def _refresh_membership_status(self, submission_id: int) -> None:
        """这次评审写入之后，重推那条领取的完成状态。

        三条评审路由（POST / PATCH / PUT / DELETE）都落在上面三个方法里，所以推进
        这条轴只有一个入口，与 ``app.domain.task.submission_state`` 同一口径。
        """
        if self._session is None:
            return
        await refresh_completion_status_for_submission(self._session, submission_id)

    async def _maybe_award_rank(
        self,
        *,
        submission_id: int,
        previous_accepted: bool | None,
        new_accepted: bool,
    ) -> bool:
        if (
            not new_accepted
            or previous_accepted is True
            or self._rank_service is None
            or self._submission_repo is None
        ):
            return False
        submission = await self._submission_repo.get_by_id(submission_id)
        if submission is None:
            return False
        space_id = None
        task_rank: int | None = None
        if self._membership_repo is not None and self._task_repo is not None:
            membership = await self._membership_repo.get_by_id(submission.membership_id)
            if membership is not None:
                task = await self._task_repo.get_by_id(membership.task_id)
                if task is not None:
                    space_id = getattr(task, "space_id", None)
                    task_rank = getattr(task, "rank", None)
        return await self._rank_service.award_rank_if_higher(
            space_id=space_id,
            user_id=submission.submitter_id,
            task_rank=task_rank,
        )
