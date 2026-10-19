"""A triager explicitly accepts feedback and rewards its human submitter."""

import uuid

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.feedback import claims
from app.domain.feedback.models import FeedbackStatus
from app.domain.feedback.services import FeedbackService
from app.domain.usage.incentives import FEEDBACK_CREDITS, CreditIncentives
from app.domain.user.services import user_by_handle


async def reward_feedback(
    service: FeedbackService, feedback_id: uuid.UUID, *, actor: str
) -> dict:
    row = await claims.lock(service._session, feedback_id)
    if row is None or row.deleted_at is not None:
        raise NotFoundError(say("feedbackNotFound"))
    if row.status == FeedbackStatus.declined:
        raise BadRequestError(say("feedbackRewardDeclined"))
    handle = row.submitted_by_handle if row.author_is_agent else row.author_handle
    user = await user_by_handle(service._session, handle) if handle else None
    if user is None:
        raise BadRequestError(say("feedbackRewardHuman"))
    result = await CreditIncentives(service._session).issue(
        key=f"feedback:{row.id}",
        user_id=user.id,
        credits=FEEDBACK_CREDITS,
        reason=f"Accepted feedback FB-{row.display_no}",
        actor=actor,
    )
    await service.advance(row, FeedbackStatus.in_progress, by_handle=actor)
    return result
