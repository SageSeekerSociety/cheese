"""Cross-session roster reuse must survive unrelated writes, not membership writes."""

import asyncio
import re
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import copy_context

import pytest
from sqlalchemy import delete, event, text, update

from app.core.query_watch import request_scope
from app.core.request_facts import recall, remember
from app.domain.agent_instance.adapters import project_team
from app.domain.idempotency.store import claim
from app.domain.membership.ports import TeammateSeat
from app.domain.membership.repositories import MemberRepository
from app.domain.membership.roster import roster
from app.domain.project.models import Project, ProjectMember
from tests.integration.conftest import (
    add_external_member,
    post_project,
    session_auth_headers,
)


@contextmanager
def _statements(engine):
    sent = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        sent.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield sent
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _roster_reads(sent):
    return sum(
        sql.lstrip().upper().startswith("SELECT")
        and bool(re.search(r"\bFROM\s+project_members\b", sql))
        for sql in sent
    )


@pytest.fixture
def project(client):
    response = post_project(client, {"name": "Roster reads"}, owner="alice")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_mentioned_message_reads_roster_once(client, project):
    with _statements(client.test_app_engine.sync_engine) as sent:
        response = client.post(
            f"/topics/{project['root_topic_id']}/messages",
            json={"content": "<@alice> hello", "request_id": str(uuid.uuid4())},
            headers=session_auth_headers("alice"),
        )
    assert response.status_code == 200, response.text
    message = response.json()["data"]
    assert message["content"] == "<@alice> hello"
    assert message["author"] == "alice"
    assert message["refs"] == ["user:alice"]
    assert message["meta"]["agent_recipient"]["mentioned"] is False
    counts = Counter()
    for sql in sent:
        for table in ("project_members", "user_profile", "own_agents"):
            if table == "user_profile" and "project_members" not in sql:
                continue  # Profiles joined to the roster, not other profile reads.
            if re.search(rf"\b(?:FROM|JOIN)\s+{table}\b", sql):
                counts[table] += 1
    print("SQL_TOTAL", len(sent), "ROSTER_QUERIES", dict(counts))
    assert counts == dict.fromkeys(("project_members", "user_profile", "own_agents"), 1)


def test_different_teams_and_projects_do_not_share_answers(client, project):
    other = post_project(client, {"name": "Elsewhere"}, owner="alice").json()["data"]

    async def first_team(_session, _project_id):
        return [TeammateSeat("first", "first", "First", "default", True)]

    async def second_team(_session, _project_id):
        return [TeammateSeat("second", "second", "Second", "default", True)]

    async def run():
        with request_scope():
            async with client.test_factory() as first, client.test_factory() as second:
                pid = uuid.UUID(project["id"])
                a = await roster(first, pid, team=first_team)
                b = await roster(second, pid, team=second_team)
                c = await roster(second, uuid.UUID(other["id"]), team=first_team)
                assert a[-1].handle == "first"
                assert b[-1].handle == "second"
                assert c[-1].handle == "first"
                assert await roster(second, pid, team=first_team) == a

    with _statements(client.test_factory.kw["bind"].sync_engine) as sent:
        asyncio.run(run())
    assert _roster_reads(sent) == 3


def test_unrelated_insert_keeps_shared_but_forgets_session_facts(client, project):
    async def run():
        with request_scope():
            async with client.test_factory() as first, client.test_factory() as second:
                pid = uuid.UUID(project["id"])
                before = await roster(first, pid, team=project_team)
                remember(second, "session-answer", True)
                assert await claim(
                    second, uuid.uuid4().hex, action="chat_message", scope_id=str(pid)
                )
                assert recall(second, "session-answer") == (False, None)
                assert await roster(second, pid, team=project_team) == before

    with _statements(client.test_factory.kw["bind"].sync_engine) as sent:
        asyncio.run(run())
    assert _roster_reads(sent) == 1


def test_member_write_between_reads_reloads_the_roster(client, project):
    add_external_member(client, project["id"], "bob", by="alice")

    async def run(sent):
        with request_scope():
            async with client.test_factory() as first, client.test_factory() as second:
                pid = uuid.UUID(project["id"])
                before = await roster(first, pid, team=project_team)
                count = _roster_reads(sent)
                assert "bob" in {row.handle for row in before}
                await second.execute(
                    delete(ProjectMember).where(
                        ProjectMember.project_id == pid,
                        ProjectMember.user_handle == "bob",
                    )
                )
                # Even a different session must re-read, not retain its old fact.
                refreshed = await roster(first, pid, team=project_team)
                assert _roster_reads(sent) == count + 1
                # The uncommitted deletion is visible only to its writer.
                assert "bob" in {row.handle for row in refreshed}
                after = await roster(second, pid, team=project_team)
                assert "bob" not in {row.handle for row in after}

    with _statements(client.test_factory.kw["bind"].sync_engine) as sent:
        asyncio.run(run(sent))


@pytest.mark.parametrize(
    "boundary", ["commit", "rollback", "close", "savepoint", "text"]
)
def test_transaction_boundaries_and_opaque_sql_forget_shared(client, project, boundary):
    async def run(sent):
        with request_scope():
            async with client.test_factory() as session:
                pid = uuid.UUID(project["id"])
                await roster(session, pid, team=project_team)
                count = _roster_reads(sent)
                if boundary == "savepoint":
                    savepoint = await session.begin_nested()
                    await savepoint.rollback()
                elif boundary == "text":
                    await session.execute(text("SELECT 1"))
                else:
                    await getattr(session, boundary)()
                await roster(session, pid, team=project_team)
                assert _roster_reads(sent) == count + 1

    with _statements(client.test_factory.kw["bind"].sync_engine) as sent:
        asyncio.run(run(sent))


def test_autoflush_is_not_skipped_by_a_cache_hit(client, project):
    add_external_member(client, project["id"], "bob", by="alice")

    async def run(sent):
        with request_scope():
            async with client.test_factory() as session:
                pid = uuid.UUID(project["id"])
                await roster(session, pid, team=project_team)
                member = await MemberRepository(session).get(
                    project_id=pid, user_handle="bob"
                )
                await session.delete(member)
                count = _roster_reads(sent)
                after = await roster(session, pid, team=project_team)
                assert _roster_reads(sent) == count + 1
                assert "bob" not in {row.handle for row in after}

    with _statements(client.test_factory.kw["bind"].sync_engine) as sent:
        asyncio.run(run(sent))


def test_identity_map_dependencies_still_invalidate(client, project):
    async def run(sent):
        with request_scope():
            async with client.test_factory() as session:
                pid = uuid.UUID(project["id"])
                held = await session.get(Project, pid)
                assert held is not None
                await roster(session, pid, team=project_team)
                count = _roster_reads(sent)
                await session.execute(
                    update(Project).where(Project.id == pid).values(name="Changed")
                )
                await roster(session, pid, team=project_team)
                assert _roster_reads(sent) == count + 1

    with _statements(client.test_factory.kw["bind"].sync_engine) as sent:
        asyncio.run(run(sent))


@pytest.mark.parametrize("scope", ["new_request", "closed_request", "separate_task"])
def test_other_contexts_do_not_reuse_answers(client, project, scope):
    async def run(sent):
        async def read():
            async with client.test_factory() as session:
                return await roster(
                    session, uuid.UUID(project["id"]), team=project_team
                )

        async with client.test_factory() as first:
            if scope == "separate_task":
                await roster(first, uuid.UUID(project["id"]), team=project_team)
                count = _roster_reads(sent)
                await asyncio.create_task(read())
            else:
                with request_scope():
                    await roster(first, uuid.UUID(project["id"]), team=project_team)
                    inherited = copy_context()
                    count = _roster_reads(sent)
                    if scope == "new_request":
                        with request_scope():
                            await read()
                if scope == "closed_request":
                    await asyncio.create_task(read(), context=inherited)
            assert _roster_reads(sent) == count + 1

    with _statements(client.test_factory.kw["bind"].sync_engine) as sent:
        asyncio.run(run(sent))


def test_one_request_shares_plain_facts_but_not_session_bound_rows(client, project):
    async def run():
        with request_scope():
            async with client.test_factory() as first, client.test_factory() as second:
                remember(first, "plain", {"answer": [1]})
                hit, value = recall(second, "plain")
                assert hit and value == {"answer": [1]}
                value["answer"].append(2)
                assert recall(first, "plain") == (True, {"answer": [1]})
                row = await first.get(Project, uuid.UUID(project["id"]))
                remember(first, "orm", [(row, None)])
                assert recall(first, "orm")[0]
                assert recall(second, "orm") == (False, None)

    asyncio.run(run())


def test_uncommitted_membership_is_not_shared_with_another_transaction(client, project):
    add_external_member(client, project["id"], "bob", by="alice")

    async def run():
        with request_scope():
            async with client.test_factory() as first, client.test_factory() as second:
                pid = uuid.UUID(project["id"])
                await first.execute(
                    delete(ProjectMember).where(
                        ProjectMember.project_id == pid,
                        ProjectMember.user_handle == "bob",
                    )
                )
                changed = await roster(first, pid, team=project_team)
                assert "bob" not in {row.handle for row in changed}
                committed = await roster(second, pid, team=project_team)
                assert "bob" in {row.handle for row in committed}
                changed_again = await roster(first, pid, team=project_team)
                assert "bob" not in {row.handle for row in changed_again}

    asyncio.run(run())
