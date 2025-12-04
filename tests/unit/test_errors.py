import pytest
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_429_TOO_MANY_REQUESTS,
)

from app.core.errors import (
    BaseError,
    BadRequestError,
    NotFoundError,
    ForbiddenError,
    AuthenticationRequiredError,
    ConflictError,
    AccessDeniedError,
    PermissionDeniedError,
    NameAlreadyExistsError,
    QuotaExceededError,
)
from app.core.domain_errors import (
    TaskParticipantsReachedLimitError,
    TeamSizeNotEnoughError,
    TeamSizeTooLargeError,
    UserAlreadyMemberError,
    NotTeamMemberYetError,
)


class TestBaseError:
    def test_status_code_and_message(self) -> None:
        error = BaseError(400, "Test error", {"key": "value"})
        assert error.status_code == 400
        assert error.args[0] == "Test error"
        assert error.data == {"key": "value"}

    def test_name_property(self) -> None:
        error = BadRequestError("Test")
        assert error.name == "BadRequestError"

    def test_to_response_body(self) -> None:
        error = BadRequestError("Invalid input", {"field": "name"})
        body = error.to_response_body()
        assert body["code"] == HTTP_400_BAD_REQUEST
        assert body["message"] == "BadRequestError: Invalid input"
        assert body["error"]["name"] == "BadRequestError"
        assert body["error"]["message"] == "Invalid input"
        assert body["error"]["data"] == {"field": "name"}


class TestCommonErrors:
    def test_bad_request_error(self) -> None:
        error = BadRequestError("Invalid request")
        assert error.status_code == HTTP_400_BAD_REQUEST

    def test_not_found_error(self) -> None:
        error = NotFoundError()
        assert error.status_code == HTTP_404_NOT_FOUND
        assert error.args[0] == "Resource not found"

    def test_not_found_for_resource(self) -> None:
        error = NotFoundError.for_resource("team", 123)
        assert error.status_code == HTTP_404_NOT_FOUND
        assert "team" in error.args[0]
        assert error.data == {"type": "team", "id": 123}

    def test_forbidden_error(self) -> None:
        error = ForbiddenError("Access denied")
        assert error.status_code == HTTP_403_FORBIDDEN

    def test_authentication_required_error(self) -> None:
        error = AuthenticationRequiredError()
        assert error.status_code == HTTP_401_UNAUTHORIZED

    def test_conflict_error(self) -> None:
        error = ConflictError("Resource conflict")
        assert error.status_code == HTTP_409_CONFLICT

    def test_quota_exceeded_error(self) -> None:
        error = QuotaExceededError()
        assert error.status_code == HTTP_429_TOO_MANY_REQUESTS


class TestSpecializedErrors:
    def test_access_denied_error_with_context(self) -> None:
        error = AccessDeniedError(
            action="DELETE",
            resource_type="team",
            resource_id=42,
        )
        assert error.status_code == HTTP_403_FORBIDDEN
        assert error.data["action"] == "DELETE"
        assert error.data["resourceType"] == "team"
        assert error.data["resourceId"] == 42

    def test_permission_denied_error(self) -> None:
        error = PermissionDeniedError("Insufficient permissions")
        assert error.status_code == HTTP_403_FORBIDDEN
        assert "Insufficient" in error.args[0]

    def test_name_already_exists_error(self) -> None:
        error = NameAlreadyExistsError("team", "Alpha")
        assert error.status_code == HTTP_409_CONFLICT
        assert "Alpha" in error.args[0]
        assert error.data == {"type": "team", "name": "Alpha"}


class TestDomainErrors:
    def test_task_participants_reached_limit(self) -> None:
        error = TaskParticipantsReachedLimitError(task_id=1, limit=10)
        assert error.status_code == HTTP_403_FORBIDDEN
        assert error.data["taskId"] == 1
        assert error.data["limit"] == 10

    def test_team_size_not_enough(self) -> None:
        error = TeamSizeNotEnoughError(min_size=3, current_size=2)
        assert error.status_code == HTTP_403_FORBIDDEN
        assert error.data["minSize"] == 3
        assert error.data["currentSize"] == 2

    def test_team_size_too_large(self) -> None:
        error = TeamSizeTooLargeError(max_size=5, current_size=6)
        assert error.status_code == HTTP_400_BAD_REQUEST
        assert error.data["maxSize"] == 5
        assert error.data["currentSize"] == 6

    def test_user_already_member(self) -> None:
        error = UserAlreadyMemberError(team_id=1, user_id=2)
        assert error.status_code == HTTP_409_CONFLICT
        assert error.data["teamId"] == 1
        assert error.data["userId"] == 2

    def test_not_team_member_yet(self) -> None:
        error = NotTeamMemberYetError(team_id=1, user_id=2)
        assert error.status_code == HTTP_404_NOT_FOUND
        assert error.data["teamId"] == 1
        assert error.data["userId"] == 2


class TestErrorInheritance:
    def test_access_denied_inherits_forbidden(self) -> None:
        error = AccessDeniedError()
        assert isinstance(error, ForbiddenError)
        assert isinstance(error, BaseError)

    def test_name_already_exists_inherits_conflict(self) -> None:
        error = NameAlreadyExistsError("team", "Test")
        assert isinstance(error, ConflictError)
        assert isinstance(error, BaseError)
