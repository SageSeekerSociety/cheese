"""The two calls every agent makes all the time read each fact once.

A session asks for its hands (`/work-lease`) before its tool calls, and the
metering proxy asks `/llm/admission` before each model request. Each answer
used to read some facts twice, or read facts nothing in it used:

- an agent reading the room it answers in still asked who else reads that
  room (four statements about the project's roster), an answer that only
  matters for a room other than its own (`app.auth.audience`);
- a session's conversation kind was asked once for its holder and once for
  its own conversation, the same row in a room (`machine.holder._kind`);
- admission read the payer's packs and the project's budget caps, then read
  them again on the gateway key's own session for the budget the key carries
  (`routes/llm_proxy.py`);
- the room a conversation is in was asked again by the access check right
  after the route had resolved the conversation (`room_task.place`);
- the agent's credential had its signature checked again by every dependency
  and helper that read it, a dozen times per admission (`core.credentials`).
"""

from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.api.deps import get_chat_service
from app.core.sandbox_auth import SANDBOX_TOKEN
from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import session_work as work_lease
from app.domain.usage.ledger import Ledger
from tests.integration.test_gateway_usage import FakeGateway
from tests.integration.test_switch_pushes_first import _machines, _room


@contextmanager
def _statements():
    seen: list[str] = []

    def on(conn, cursor, statement, params, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", on)
    try:
        yield seen
    finally:
        event.remove(Engine, "after_cursor_execute", on)


async def _checked_moments_ago(client, room):
    """The session's hands, as a check a moment ago left them: the next ask is
    answered without the whole check (`session_work._verified`)."""
    async with client.test_factory() as db:
        row = await AgentSessionService(db).by_id(room.session_id)
        assert row is not None and row.work_lease is not None
        row.work_lease = {
            **row.work_lease,
            "verified_at": datetime.now(UTC).isoformat(),
            "setup_digest": work_lease._setup_digest({}),
        }
        await db.commit()


async def _asked_for_hands(client, monkeypatch) -> list[str]:
    room = await _room(client)
    _machines(monkeypatch)
    await _checked_moments_ago(client, room)
    body = {"env": {}, "timeout": 5}
    with _statements() as seen:
        answer = client.post(room.lease_path, headers=room.agent, json=body)
    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["target"]["generation"] == room.lease["generation"]
    return seen


async def test_an_agent_asking_for_hands_in_its_room_reads_nothing_of_the_roster(
    client, monkeypatch
):
    seen = await _asked_for_hands(client, monkeypatch)

    assert [s for s in seen if "FROM project_members" in s] == []
    assert [s for s in seen if "FROM agent_bindings" in s] == []


async def test_asking_for_hands_reads_the_conversation_kind_once(client, monkeypatch):
    seen = await _asked_for_hands(client, monkeypatch)

    assert len([s for s in seen if "SELECT conversations.kind" in s]) == 1


async def test_asking_for_hands_does_not_ask_again_which_room_it_is_in(
    client, monkeypatch
):
    """The route resolves the conversation first; the access check after it
    asks which room that conversation is in and is answered from it."""
    seen = await _asked_for_hands(client, monkeypatch)

    assert [s for s in seen if "inside.room_id" in s] == []


def _admit(client, room):
    token = room.agent["Authorization"].split()[1]
    return client.post(
        "/llm/admission",
        headers={"Authorization": f"Bearer {SANDBOX_TOKEN}"},
        json={"session": token},
    )


async def test_admission_reads_the_packs_and_caps_once(client, monkeypatch):
    gateway = FakeGateway()
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(chat, "_gateway", gateway)
    room = await _room(client)
    first = _admit(client, room)
    assert first.json()["data"]["supply"]["key"] == "sk-virt-1", first.text
    priced = list(gateway.budgets)

    with _statements() as seen:
        again = _admit(client, room)

    assert again.json()["data"]["supply"]["key"] == "sk-virt-1", again.text
    assert gateway.budgets == priced
    assert len([s for s in seen if "FROM compute_grants" in s]) == 1
    assert len([s for s in seen if "FROM project_credit_budgets" in s]) == 1


async def test_a_pack_granted_between_admissions_reprices_the_key(client, monkeypatch):
    """The budget admission hands the key's read path is the one it just
    read: a pack granted since the last admission still moves the key."""
    gateway = FakeGateway()
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(chat, "_gateway", gateway)
    room = await _room(client)
    assert _admit(client, room).json()["data"]["allow"] is True
    (before,) = gateway.budgets

    async with client.test_factory() as db:
        await Ledger(db).grant_earmark(
            project_id=room.project_id, source_task_id=None, credits_total=500
        )
        await db.commit()
    again = _admit(client, room)

    assert again.json()["data"]["supply"]["key"] == "sk-virt-1", again.text
    assert len(gateway.budgets) == 2
    assert gateway.budgets[1][1] > before[1]


async def test_a_budget_admission_cannot_work_out_is_left_to_the_key(
    client, monkeypatch
):
    """Admission working out the key's budget is only a shortcut: when it
    fails, the key's own path answers as before. Raised from the route it
    would be a 500, which the metering proxy reads as the backend being
    unreachable and lets the call through unchecked."""
    gateway = FakeGateway()
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(chat, "_gateway", gateway)
    room = await _room(client)
    worked_out = type(chat)._gateway_budget_target
    calls = []

    async def first_fails(session, project_id):
        calls.append(project_id)
        if len(calls) == 1:
            raise RuntimeError("plan row missing")
        return await worked_out(chat, session, project_id)

    monkeypatch.setattr(chat, "_gateway_budget_target", first_fails)
    answer = _admit(client, room)

    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["allow"] is True
    assert answer.json()["data"]["supply"]["key"] == "sk-virt-1"
    assert len(calls) >= 2


async def test_admission_checks_the_agents_signature_once(client, monkeypatch):
    from app.core import credentials

    gateway = FakeGateway()
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(chat, "_gateway", gateway)
    room = await _room(client)
    decode = credentials.jwt.decode
    signed: list[str] = []

    def counted(token, *args, **kwargs):
        if (kwargs.get("options") or {}).get("verify_signature", True):
            signed.append(token)
        return decode(token, *args, **kwargs)

    monkeypatch.setattr(credentials.jwt, "decode", counted)
    answer = _admit(client, room)

    assert answer.json()["data"]["allow"] is True, answer.text
    token = room.agent["Authorization"].split()[1]
    assert signed.count(token) == 1


async def test_admission_puts_the_packs_select_together_once(client, monkeypatch):
    """The second read of the payer's packs is answered from the request
    before its select is put together (about 150 µs a time)."""
    from app.domain.usage import ledger_queries

    gateway = FakeGateway()
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(chat, "_gateway", gateway)
    room = await _room(client)
    _admit(client, room)
    built: list[object] = []
    select = ledger_queries.select

    def counted(*args, **kwargs):
        built.append(args)
        return select(*args, **kwargs)

    monkeypatch.setattr(ledger_queries, "select", counted)
    answer = _admit(client, room)

    assert answer.json()["data"]["allow"] is True, answer.text
    assert len([a for a in built if a == (ledger_queries.ComputeGrant,)]) == 1


async def test_admission_answers_a_key_in_step_from_the_project_it_read(
    client, monkeypatch
):
    """The project's settings read at the top of an admission already name the
    key and the budget it carries; the key's own read path would only read the
    project again to find the same. One read of the project, and one statement
    for the room the credential works in."""
    gateway = FakeGateway()
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(chat, "_gateway", gateway)
    room = await _room(client)
    assert _admit(client, room).json()["data"]["supply"]["key"] == "sk-virt-1"

    with _statements() as seen:
        again = _admit(client, room)

    assert again.json()["data"]["supply"]["key"] == "sk-virt-1", again.text
    assert len([s for s in seen if "FROM projects" in s]) == 1
    assert [s for s in seen if s.startswith("SELECT inside.room_id")] == []


async def test_admission_reads_the_paying_team_with_its_plan(client, monkeypatch):
    """Who pays for a project's model calls is its team on its plan: one
    statement for both, where the team and the plan were two."""
    gateway = FakeGateway()
    chat = client.app.dependency_overrides[get_chat_service]()
    monkeypatch.setattr(chat, "_gateway", gateway)
    room = await _room(client)
    _admit(client, room)

    with _statements() as seen:
        again = _admit(client, room)

    assert again.json()["data"]["allow"] is True, again.text
    teams = [s for s in seen if "FROM team" in s and "team_user_relation" not in s]
    assert len(teams) == 1, teams
    assert "JOIN plans" in teams[0]
    assert [s for s in seen if s.startswith("SELECT plans.")] == []
