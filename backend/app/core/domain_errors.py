from app.core.errors import (
    BadRequestError,
    ForbiddenError,
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


class TaskParticipantsReachedLimitError(ForbiddenError):
    def __init__(self, task_id: int, limit: int) -> None:
        super().__init__(
            message=f"Task participants limit ({limit}) reached",
            data={"taskId": task_id, "limit": limit},
        )


class TeamLockedError(ForbiddenError):
    def __init__(self, team_id: int) -> None:
        super().__init__(
            message="Team is locked and cannot be modified",
            data={"teamId": team_id},
        )
