"""Every error this server answers with is a BaseError, said one way.

AppError and its three subclasses are deprecated and on their way out; until
their raises have moved, each of them must already BE the class replacing it,
so an ``except`` written for either catches both, and must answer with the
same envelope as everything else.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    AppError,
    AuthenticationRequiredError,
    BaseError,
    GatewayTimeoutError,
    GatewayUnavailableError,
    NotFoundError,
    SystemBusyError,
    UnauthorizedError,
    UnprocessableEntityError,
    UpstreamUnavailableError,
    ValidationError,
    register_exception_handlers,
)


@pytest.mark.parametrize(
    ("legacy", "replacement", "status"),
    [
        (ValidationError, UnprocessableEntityError, 422),
        (UnauthorizedError, AuthenticationRequiredError, 401),
        (GatewayUnavailableError, UpstreamUnavailableError, 503),
    ],
)
def test_a_deprecated_class_is_its_replacement(legacy, replacement, status) -> None:
    error = legacy("said")
    assert isinstance(error, replacement)
    assert isinstance(error, BaseError)
    assert error.status_code == status
    assert error.message == "said"
    assert legacy().message == legacy.message  # the class's default stands in


def test_the_code_is_the_class_name_unless_declared() -> None:
    class Declared(NotFoundError):
        code = "SomethingElse"

    class Inherits(Declared):
        pass

    assert NotFoundError.code == "NotFoundError"
    assert ValidationError.code == "ValidationError"
    assert Declared().to_response_body()["error"]["name"] == "SomethingElse"
    assert Inherits.code == "Inherits"


def test_retryable_is_what_the_class_says() -> None:
    class Passing(UpstreamUnavailableError):
        retryable = True

    assert Passing().to_response_body()["error"]["retryable"] is True
    assert GatewayTimeoutError().to_response_body()["error"]["retryable"] is True
    # Raised for a missing configuration as often as for load: waiting does not
    # fix those, so neither says it does.
    assert SystemBusyError().to_response_body()["error"]["retryable"] is False
    assert GatewayUnavailableError().to_response_body()["error"]["retryable"] is False


def _client(exc: Exception) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "exc",
    [ValidationError("bad input"), UnprocessableEntityError("bad input")],
)
def test_both_families_answer_with_the_same_envelope(exc: BaseError) -> None:
    response = _client(exc).get("/boom")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert body["message"] == "bad input"
    assert "data" not in body
    assert body["error"]["name"] == type(exc).__name__
    assert body["error"]["message"] == "bad input"
    assert body["error"]["retryable"] is False


def test_a_deprecated_error_answers_a_stream_with_an_event() -> None:
    response = _client(GatewayUnavailableError("down")).get(
        "/boom", headers={"accept": "text/event-stream"}
    )
    assert response.status_code == 503
    assert response.text.startswith("event: error")


def test_app_error_has_no_handler_of_its_own() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    assert AppError not in app.exception_handlers
