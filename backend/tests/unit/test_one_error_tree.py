"""Every error this server answers with is a BaseError, said one way.

AppError and its three subclasses are deprecated and on their way out; until
their raises have moved, each of them must already BE the class replacing it,
so an ``except`` written for either catches both, and must answer with the
same envelope as everything else.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

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


def test_the_wire_name_is_the_class_name_unless_declared() -> None:
    class Declared(NotFoundError):
        wire_name = "SomethingElse"

    class Inherits(Declared):
        pass

    assert NotFoundError().name == "NotFoundError"
    assert ValidationError().name == "ValidationError"
    assert Declared().to_response_body()["error"]["name"] == "SomethingElse"
    # A frozen name is the class's own promise; a subclass is a new condition.
    assert Inherits().name == "Inherits"


def test_retryable_is_what_the_class_says() -> None:
    class Passing(UpstreamUnavailableError):
        retryable = True

    assert Passing().to_response_body()["error"]["retryable"] is True
    # An execution that timed out may have run; resending it is not the same
    # request.
    assert GatewayTimeoutError().to_response_body()["error"]["retryable"] is False
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


def test_a_deprecated_error_answers_a_stream_with_the_same_envelope() -> None:
    """A refusal asked for as a stream carries the same body a JSON answer
    would, so a caller switches on ``error.name`` and ``error.retryable``
    whichever way it asked instead of reading the sentence."""
    refused = GatewayUnavailableError("down")
    streamed = _client(refused).get("/boom", headers={"accept": "text/event-stream"})
    assert streamed.status_code == 503
    event, data = streamed.text.strip().split("\n")
    assert event == "event: error"
    assert (
        json.loads(data.removeprefix("data: ")) == _client(refused).get("/boom").json()
    )


def test_a_request_refused_by_validation_answers_a_stream_with_the_same_envelope() -> (
    None
):
    app = FastAPI()
    register_exception_handlers(app)

    class Payload(BaseModel):
        count: int

    @app.post("/count")
    async def count(payload: Payload) -> Payload:
        return payload

    client = TestClient(app, raise_server_exceptions=False)
    refused = {"json": {"count": "many"}}
    streamed = client.post("/count", headers={"accept": "text/event-stream"}, **refused)
    assert streamed.status_code == 400
    event, data = streamed.text.strip().split("\n")
    assert event == "event: error"
    assert (
        json.loads(data.removeprefix("data: "))
        == client.post("/count", **refused).json()
    )


def test_app_error_has_no_handler_of_its_own() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    assert AppError not in app.exception_handlers
