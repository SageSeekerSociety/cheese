from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)


class RealNameInfoRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__("Real name information is required for this task")


class NotTaskParticipantYetError(NotFoundError):
    def __init__(self, task_id: int, member_id: int) -> None:
        super().__init__(
            message="Not a task participant yet",
            data={"taskId": task_id, "memberId": member_id},
        )


class AlreadyBeTaskParticipantError(ConflictError):
    def __init__(self, task_id: int, member_id: int) -> None:
        super().__init__(
            message="Already a task participant",
            data={"taskId": task_id, "memberId": member_id},
        )


class TaskParticipantNotApprovedError(ForbiddenError):
    def __init__(self, task_id: int, member_id: int) -> None:
        super().__init__(
            message="Task participant not approved",
            data={"taskId": task_id, "memberId": member_id},
        )


class YourRankIsNotHighEnoughError(BadRequestError):
    def __init__(self, required_rank: int, current_rank: int) -> None:
        super().__init__(
            message=f"Your rank ({current_rank}) is not high enough. Required: {required_rank}",  # noqa: E501
            data={"requiredRank": required_rank, "currentRank": current_rank},
        )


class TeamSizeNotEnoughError(ForbiddenError):
    def __init__(self, min_size: int, current_size: int) -> None:
        super().__init__(
            message=f"Team size ({current_size}) is below minimum ({min_size})",
            data={"minSize": min_size, "currentSize": current_size},
        )


class TeamSizeTooLargeError(BadRequestError):
    def __init__(self, max_size: int, current_size: int) -> None:
        super().__init__(
            message=f"Team size ({current_size}) exceeds maximum ({max_size})",
            data={"maxSize": max_size, "currentSize": current_size},
        )


class TaskSubmissionNotEditableError(BadRequestError):
    def __init__(self, task_id: int) -> None:
        super().__init__(
            message="Task submission is not editable",
            data={"taskId": task_id},
        )


class TaskSubmissionNotMatchSchemaError(BadRequestError):
    def __init__(self, details: str | None = None) -> None:
        super().__init__(
            message="Task submission does not match schema",
            data={"details": details} if details else None,
        )


class TaskRegistrationNotStartedError(ForbiddenError):
    def __init__(self, task_id: int) -> None:
        super().__init__(
            message="Task registration has not started yet",
            data={"taskId": task_id},
        )


class TaskParticipantsReachedLimitError(ForbiddenError):
    def __init__(self, task_id: int, limit: int) -> None:
        super().__init__(
            message=f"Task participants limit ({limit}) reached",
            data={"taskId": task_id, "limit": limit},
        )


class EmailOrPhoneRequiredError(BadRequestError):
    def __init__(self) -> None:
        super().__init__("Email or phone is required")


class YourTeamMemberRankIsNotHighEnoughError(ForbiddenError):
    def __init__(self, member_id: int, required_rank: int, current_rank: int) -> None:
        super().__init__(
            message=f"Team member rank ({current_rank}) is not high enough. Required: {required_rank}",  # noqa: E501
            data={
                "memberId": member_id,
                "requiredRank": required_rank,
                "currentRank": current_rank,
            },
        )


class TeamLockedError(ForbiddenError):
    def __init__(self, team_id: int) -> None:
        super().__init__(
            message="Team is locked and cannot be modified",
            data={"teamId": team_id},
        )


class TeamRoleConflictError(ConflictError):
    def __init__(
        self, team_id: int, user_id: int, current_role: str, requested_role: str
    ) -> None:
        super().__init__(
            message=f"Role conflict: user already has role {current_role}",
            data={
                "teamId": team_id,
                "userId": user_id,
                "currentRole": current_role,
                "requestedRole": requested_role,
            },
        )


class UserAlreadyMemberError(ConflictError):
    def __init__(self, team_id: int, user_id: int) -> None:
        super().__init__(
            message="User is already a team member",
            data={"teamId": team_id, "userId": user_id},
        )


class NotTeamMemberYetError(NotFoundError):
    def __init__(self, team_id: int, user_id: int) -> None:
        super().__init__(
            message="User is not a team member",
            data={"teamId": team_id, "userId": user_id},
        )


class PendingApplicationExistsError(ConflictError):
    def __init__(self, team_id: int, user_id: int) -> None:
        super().__init__(
            message="A pending application already exists",
            data={"teamId": team_id, "userId": user_id},
        )


class NotSpaceAdminYetError(NotFoundError):
    def __init__(self, space_id: int, user_id: int) -> None:
        super().__init__(
            message="User is not a space admin",
            data={"spaceId": space_id, "userId": user_id},
        )


class AlreadyBeSpaceAdminError(ConflictError):
    def __init__(self, space_id: int, user_id: int) -> None:
        super().__init__(
            message="User is already a space admin",
            data={"spaceId": space_id, "userId": user_id},
        )


class AlreadyBeSpaceOwnerError(ConflictError):
    def __init__(self, space_id: int, user_id: int) -> None:
        super().__init__(
            message="User is already the space owner",
            data={"spaceId": space_id, "userId": user_id},
        )


class RankNotEnabledForSpaceError(BadRequestError):
    def __init__(self, space_id: int) -> None:
        super().__init__(
            message="Rank is not enabled for this space",
            data={"spaceId": space_id},
        )


class ConversationNotFoundError(NotFoundError):
    def __init__(self, conversation_id: str) -> None:
        super().__init__(
            message="Conversation not found",
            data={"conversationId": conversation_id},
        )
