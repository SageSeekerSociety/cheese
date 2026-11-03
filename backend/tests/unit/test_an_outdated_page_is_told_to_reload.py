"""A page built for an older API is refused with a code it can act on; every
other caller is answered as before.

The rule (``app.core.api_generation``): a request carrying an API generation
below the backend's is refused — 409 ``ClientOutdated`` over HTTP, close code
4409 on a WebSocket — and one carrying the current generation, or none at all
(the CLI, MCP, the connector, scripts), reaches the routes.

Asked of the app itself, at a path no route serves: the refusal comes before
routing, and a request let through is answered by the router's 404 (a
WebSocket by its plain close), so nothing here needs a database.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.api_generation import CURRENT
from app.main import app

_NOWHERE = "/no-such-route-for-the-generation-test"


def test_a_page_below_the_generation_is_refused_with_its_code():
    response = TestClient(app).post(
        _NOWHERE, headers={"X-Cheese-Api-Gen": str(CURRENT - 1)}
    )

    assert response.status_code == 409
    assert response.json()["error"]["name"] == "ClientOutdated"


@pytest.mark.parametrize("headers", [{"X-Cheese-Api-Gen": str(CURRENT)}, {}])
def test_a_current_page_and_a_caller_that_says_nothing_reach_the_routes(headers):
    assert TestClient(app).post(_NOWHERE, headers=headers).status_code == 404


def _close_code(query: str) -> int:
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        TestClient(app).websocket_connect(f"{_NOWHERE}{query}") as socket,
    ):
        socket.receive_text()
    return closed.value.code


def test_an_outdated_page_s_websocket_closes_with_a_code_it_can_read():
    assert _close_code(f"?api_gen={CURRENT - 1}") == 4409


@pytest.mark.parametrize("query", [f"?api_gen={CURRENT}", ""])
def test_a_current_page_s_websocket_and_one_without_a_number_reach_the_routes(query):
    assert _close_code(query) != 4409
