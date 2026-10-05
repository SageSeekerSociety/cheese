"""A selection question in a room that seats no agent is still answered.

The project's own agent answers it, and its session reaches the model, though
that agent sits in no room for it. The agent taken off that room is still
refused the model with its own session credential.

The session host is faked at its boundary, as in test_doc_agent; the session's
model call goes to the platform's model route the way a real session's does,
with the credential the session was started with.
"""

import httpx

from app.domain.agent.harness.channel import mint_session_token
from app.main import app
from tests.integration.conftest import session_auth_headers
from tests.integration.test_a_removed_agent_loses_its_session_channels import (
    model_pool as model_pool,  # noqa: F401
)
from tests.integration.test_doc_agent import sessions as sessions  # noqa: F401
from tests.integration.test_doc_agent_box import _ask, _done, _selection
from tests.integration.test_doc_edits import _document

#: Taken before `model_pool` swaps `httpx.AsyncClient` for the fake upstream.
_Client = httpx.AsyncClient


async def _model_call(credential: str) -> int:
    async with _Client(
        transport=httpx.ASGITransport(app=app), base_url="http://platform"
    ) as platform:
        response = await platform.post(
            "/llm/v1/messages",
            headers={"Authorization": f"Bearer {credential}"},
            content=b'{"model":"m","messages":[]}',
        )
    return response.status_code


def test_a_room_with_no_agent_still_gets_its_selection_question_answered(
    client, sessions, model_pool
):
    room, seat = _document(client)
    project = client.get(f"/topics/{room}", headers=session_auth_headers("alice"))
    project_id = project.json()["data"]["project_id"]
    removed = client.delete(
        f"/topics/{room}/members/{seat}", headers=session_auth_headers("alice")
    )
    assert removed.status_code == 200, removed.text

    launched: list[str] = []
    start = sessions.start

    async def remember_the_credential(ref, spec, access, **kw):
        launched.append(access.credential)
        return await start(ref, spec, access, **kw)

    sessions.start = remember_the_credential

    async def ask_the_model(credential, question):
        return f"model {await _model_call(launched[-1])}", None

    sessions.script = ask_the_model

    status, events = _ask(client, room, preset="check", selection=_selection("范围"))

    assert status == 200
    assert _done(events)["answer"] == "model 200"
    off_the_room = client.post(
        "/llm/v1/messages",
        headers={
            "Authorization": f"Bearer {mint_session_token(project_id, room, seat)}"
        },
        content=b'{"model":"m","messages":[]}',
    )
    assert off_the_room.status_code == 403
