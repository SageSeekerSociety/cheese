import logging

from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_429_TOO_MANY_REQUESTS,
)
from starlette.testclient import TestClient

from app.core.domain_errors import (
    NotTeamMemberYetError,
    TaskParticipantsReachedLimitError,
    TeamSizeNotEnoughError,
    TeamSizeTooLargeError,
    UserAlreadyMemberError,
)
from app.core.errors import (
    AccessDeniedError,
    AuthenticationRequiredError,
    BadRequestError,
    BaseError,
    ConflictError,
    ForbiddenError,
    NameAlreadyExistsError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
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
        assert body["message"] == "Invalid input"
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


class TestUnhandledExceptionHandler:
    """一个没被任何 except 兜住的异常必须还是一个我们自己的错误响应。

    连接池被打满时抛出的 TimeoutError 谁也没接，Starlette 于是回了 21 字节的纯文
    本 `Internal Server Error`——前端拿到的不是它认得的 `{code, message, data}`，
    解析失败之后连「出错了」都说不出来，而后端日志里什么都没有。
    """

    def _client(self) -> TestClient:
        from fastapi import FastAPI

        from app.core.errors import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/boom")
        async def _boom() -> None:
            raise TimeoutError("QueuePool limit of size 5 overflow 10 reached")

        return TestClient(app, raise_server_exceptions=False)

    def test_unhandled_error_answers_in_our_envelope(self) -> None:
        response = self._client().get("/boom")
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body["code"] == 500
        assert "data" not in body
        assert body["message"]

    def test_unhandled_error_does_not_leak_its_message(self) -> None:
        """出了什么事写进日志，不写进给发起请求的人的回复里。"""
        body = self._client().get("/boom").json()
        assert "QueuePool" not in body["message"]

    def test_unhandled_error_is_said_in_the_reader_s_language(self) -> None:
        """The screen renders ``error.i18n``; without it an English reader
        got the Chinese sentence."""
        body = self._client().get("/boom").json()
        assert body["error"]["i18n"] == {"key": "serverInternalError", "params": {}}
        assert body["error"]["message"] == body["message"]


class TestConditionsThatAreNotThisServerSFault:
    """A 500 says 「this server broke」. Two things that are not that were
    reaching the catch-all and saying it anyway — and, because an unhandled
    exception is logged on three separate ways out of a request, saying it three
    times into the alert channel.
    """

    def _client(self) -> TestClient:
        from fastapi import FastAPI
        from starlette.requests import ClientDisconnect

        from app.core.errors import register_exception_handlers
        from app.domain.agent.device_hub import DeviceOffline

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/offline")
        async def _offline() -> None:
            raise DeviceOffline("machine-7")

        @app.get("/hung-up")
        async def _hung_up() -> None:
            raise ClientDisconnect()

        return TestClient(app, raise_server_exceptions=False)

    def test_a_device_that_is_off_is_answered_not_blamed_on_the_server(self) -> None:
        response = self._client().get("/offline")
        assert response.status_code == 409
        assert response.headers["X-Device-Id"] == "machine-7"
        assert "machine-7" in response.json()["message"]

    def test_the_offline_answer_names_the_condition_clients_switch_on(self) -> None:
        body = self._client().get("/offline").json()
        assert body["error"]["name"] == "DeviceOffline"

    def test_a_device_that_is_off_is_not_logged_as_an_error(self, caplog) -> None:
        """This was 162 of the alert channel's first 600 messages — more than a
        quarter of everything it said, for a state with nothing to fix."""
        with caplog.at_level(logging.DEBUG):
            self._client().get("/offline")
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []

    def test_a_browser_that_hung_up_is_not_an_error_either(self, caplog) -> None:
        """A tab closed mid-upload. Nobody is left to receive an answer, and
        nothing here failed."""
        with caplog.at_level(logging.DEBUG):
            response = self._client().get("/hung-up")
        assert response.status_code == 499
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []

    def test_each_answer_carries_the_key_a_screen_renders(self) -> None:
        client = self._client()
        offline = client.get("/offline").json()["error"]["i18n"]
        hung_up = client.get("/hung-up").json()["error"]["i18n"]
        assert offline == {"key": "deviceOffline", "params": {"device": "machine-7"}}
        assert hung_up == {"key": "clientDisconnected", "params": {}}


class TestABrowserThatHangsUpOnAContentHost:
    """Previews and published Sites are answered by middleware that sits
    outside the platform's exception handlers. A dev server's page reloading
    cancels its module fetches by the dozen, and each one reached the
    catch-all as two ERROR records and a page for a fault that never happened.
    """

    @staticmethod
    def _hang_up(host: str, monkeypatch) -> list[dict]:
        import anyio
        from fastapi import FastAPI

        from app.api.preview_host import AUTH_PATH, PreviewHostMiddleware
        from app.core.config import settings
        from app.core.errors import register_exception_handlers
        from app.domain.site.hosting import SiteHostMiddleware

        monkeypatch.setattr(settings, "sites_domain", "content.example.net")
        monkeypatch.setattr(settings, "frontend_url", "https://cheese.example.com")
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(SiteHostMiddleware, platform=app)
        app.add_middleware(PreviewHostMiddleware, platform=app)

        sent: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": AUTH_PATH,
            "raw_path": AUTH_PATH.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", host.encode()),
                (b"origin", b"https://cheese.example.com"),
            ],
            "client": ("203.0.113.9", 50000),
            "server": (host, 443),
        }
        anyio.run(app, scope, receive, send)
        return sent

    def test_on_a_preview_it_is_not_an_error(self, caplog, monkeypatch) -> None:
        host = "preview-" + "a" * 32 + ".content.example.net"
        with caplog.at_level(logging.DEBUG):
            self._hang_up(host, monkeypatch)
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []

    def test_on_a_site_it_is_not_an_error(self, caplog, monkeypatch) -> None:
        host = "b" * 32 + ".content.example.net"
        with caplog.at_level(logging.DEBUG):
            self._hang_up(host, monkeypatch)
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
