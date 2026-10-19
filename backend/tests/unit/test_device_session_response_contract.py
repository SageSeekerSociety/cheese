"""Both clients can generate types for the raw running-session response."""

import pytest
from fastapi import FastAPI

from app.api.routes.connector.running_sessions import router


@pytest.mark.parametrize(
    "path",
    [
        "/connector/device/running-sessions",
        "/connector/devices/{device_id}/running-sessions",
    ],
)
def test_running_session_readers_publish_their_raw_response(path):
    app = FastAPI()
    app.include_router(router)
    document = app.openapi()
    schema = document["paths"][path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert "$ref" in schema
    schemas = document["components"]["schemas"]
    response = schemas[schema["$ref"].rsplit("/", 1)[1]]
    assert response["required"] == ["sessions"]
    assert "code" not in response["properties"]
    sessions = response["properties"]["sessions"]
    assert sessions["type"] == "array"
    session = schemas[sessions["items"]["$ref"].rsplit("/", 1)[1]]
    assert set(session["required"]) == {
        "session_id",
        "agent_name",
        "agent_name_source",
        "project_id",
        "project_name",
        "team_id",
        "team_name",
        "conversation_id",
        "conversation_kind",
        "conversation_title",
        "room_id",
        "roles",
        "since",
    }
    assert set(session["properties"]["roles"]["items"]["enum"]) == {
        "center",
        "executor",
    }
