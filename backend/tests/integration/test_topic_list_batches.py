"""`GET /topics?project_id=` batches its per-project reads once across the page.

The topic list renders every channel's row together with its sidebar signals
(last activity, relevance, live cards, waits, unanswered questions, 支线,
replied-to-me, unresolved failures, and `my_tasks` — both the caller's own
underway tasks and the borrowed `filling_in` rows). Several of those helpers
ask the same underlying question:

- which 支线 belong to these rooms (`threads_of_rooms`, `replied_to_me`,
  `threads_still_threads`);
- which underway tasks belong to these rooms and when each last spoke
  (`tasks_of_rooms` twice, `underway_counts`, `underway_with`,
  `underway_others`, `last_block_at_for_tasks` twice);
- which conversations sit on an unanswered question (`_awaiting_an_answer`,
  and `project_task_rows` for both `mine` and `picked`);
- which acceptance card each sidebar task rides on (`latest_cards_by_task` for
  both `mine` and `picked`);
- how many topics the project has (`list_for_project_with_activity` already
  fetches the full unpaginated list with `environment` and
  `default_environment` deferred, so a second `count_for_project` round-trip
  re-runs the same predicate).

Each of those questions is answered in one batched query per request rather
than repeated across helpers or looped per channel, while every response field
is preserved.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from app.api.room_snapshot import room_snapshot
from app.domain.agent.models import AgentTurn
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.services import threads_of_rooms, threads_still_threads
from app.domain.event_author import AuthorType
from app.domain.identity.actor import Actor
from app.domain.review.models import AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.services import TaskService
from app.domain.thread.services import open_thread
from app.domain.topic.services import TopicService
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.support.counting_session import counting_sql

EXPECTED_TOPIC_KEYS = {
    "accepted_at",
    "accepted_by",
    "activity",
    "agent_handle",
    "archived_at",
    "awaits_me",
    "can_manage",
    "cleanup_due_at",
    "created_at",
    "description",
    "id",
    "incomplete_since",
    "joined",
    "kind",
    "last_activity_at",
    "members_only",
    "moving",
    "my_tasks",
    "number",
    "parent_id",
    "presentation",
    "project_id",
    "replied_to_me",
    "speaker_roles",
    "status",
    "thread_replies_open",
    "title",
    "unseen_delivery",
    "updated_at",
    "upgraded_from_block_id",
    "waits",
}

EXPECTED_RAIL_TASK_KEYS = {
    "accepted_at",
    "accepted_by",
    "agent_handle",
    "agent_left_at",
    "awaits_me",
    "base_branch",
    "base_task_id",
    "branch_name",
    "card",
    "closed_at",
    "conclusion",
    "contributor_handles",
    "created_at",
    "created_by",
    "created_by_assistant",
    "delivered_head",
    "document_id",
    "historical_delivery_id",
    "id",
    "incomplete_since",
    "last_activity_at",
    "moving",
    "needs_my_action",
    "number",
    "owner_handle",
    "pr_number",
    "pr_url",
    "presentation",
    "progress_note",
    "progress_set_at",
    "progress_set_by",
    "progress_state",
    "project_id",
    "reporter_handle",
    "reviewer_handle",
    "room_id",
    "source_block_id",
    "stalled",
    "status",
    "title",
    "title_source",
    "unseen_delivery",
    "updated_at",
    "upgraded_from_block_id",
}

_TOPICS_SELECT = re.compile(r"^\s*SELECT\b[^;]*?\bFROM topics\b", re.IGNORECASE)
_THREADS_SELECT = re.compile(
    r"^\s*SELECT threads\.[^;]*?\bFROM threads\s+WHERE threads\.room_id",
    re.IGNORECASE,
)
_TASK_CARDS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM accept_cards\s+WHERE accept_cards\.task_id",
    re.IGNORECASE,
)
_AWAITING_PLACES_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM\s+\(SELECT DISTINCT ON \(blocks\.conversation_id\)",
    re.IGNORECASE,
)
_UNDERWAY_TASKS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM tasks\s+WHERE tasks\.room_id IN\b[^;]*?\btasks\.closing_since IS NULL",
    re.IGNORECASE,
)
_ALL_TASKS_SELECT = re.compile(
    r"^\s*SELECT tasks\.[^;]*?\bFROM tasks\b",
    re.IGNORECASE,
)
_PROJECTS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM projects\b",
    re.IGNORECASE,
)
_AGENT_INSTANCES_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM agent_instances\b",
    re.IGNORECASE,
)
_AGENT_BINDINGS_IN_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM agent_bindings\s+WHERE agent_bindings\.user_id IN\b",
    re.IGNORECASE,
)
_SESSION_STATES_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM session_states\b",
    re.IGNORECASE,
)
_PROJECT_MEMBERS_COUNT_SELECT = re.compile(
    r"^\s*SELECT count\(\*\)\b[^;]*?\bFROM project_members\b",
    re.IGNORECASE,
)
_EXCLUSIONS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM project_member_exclusions\b",
    re.IGNORECASE,
)
_TEAM_MEMBERS_PER_TEAM_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM team_user_relation\s+WHERE team_user_relation\.team_id =",
    re.IGNORECASE,
)
_USERS_IN_SELECT = re.compile(
    r'^\s*SELECT\b[^;]*?\bFROM "user"\s+WHERE "user"\.id IN\b',
    re.IGNORECASE,
)
_TEAM_USER_REL_FOR_USER_SELECT = re.compile(
    r"^\s*SELECT team_user_relation\.id\b[^;]*?\bFROM team_user_relation\s+WHERE team_user_relation\.user_id =",
    re.IGNORECASE,
)
_EMPTY_IN_NULL_SELECT = re.compile(
    r"IN \(NULL\)",
    re.IGNORECASE,
)
_ROOM_OF_UNION_SELECT = re.compile(
    r"^\s*SELECT inside\.room_id FROM",
    re.IGNORECASE,
)
_TOPIC_MEMBERSHIPS_SELECT = re.compile(
    r"^\s*SELECT topic_memberships\b[^;]*?\bFROM topic_memberships\b",
    re.IGNORECASE,
)
_USER_PROFILE_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM user_profile\b",
    re.IGNORECASE,
)
_FAILED_TURN_BLOCKS_SELECT = re.compile(
    r"^\s*SELECT blocks\.conversation_id, blocks\.turn_id, blocks\.created_at FROM blocks\b",
    re.IGNORECASE,
)
_PROJECT_FORGES_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM project_forges\b",
    re.IGNORECASE,
)
_ACCEPT_CARDS_ALL_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM accept_cards\b",
    re.IGNORECASE,
)
_BLOCKS_BY_IDS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM blocks\s+WHERE blocks\.id = ANY\b",
    re.IGNORECASE,
)
_ROUTINE_RUNS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM routine_runs\b",
    re.IGNORECASE,
)
_ACCEPT_CARD_REVIEWER_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM accept_card_reviewer\b",
    re.IGNORECASE,
)
_TASK_LAST_SAID_SELECT = re.compile(
    r"^\s*SELECT tasks\.id,\s*\(SELECT max\(blocks\.created_at\)",
    re.IGNORECASE,
)
_PROJECT_ROLE_DEFS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM project_role_defs\b",
    re.IGNORECASE,
)


def _count(statements: list[str], pattern: re.Pattern[str]) -> int:
    return sum(1 for s in statements if pattern.search(s))


def _seed_populated_project(client) -> tuple[str, list[str]]:
    """A project with four channels, each holding 支线, own tasks, others'
    tasks, acceptance cards, and open questions."""
    pid = post_project(client, json={"name": "Batched list"}, owner="alice").json()[
        "data"
    ]["id"]
    for handle in ("bob", "carol"):
        join_project_team(client, pid, handle)

    async def _seed(proj_uuid: uuid.UUID) -> list[str]:
        seeded_rooms: list[str] = []
        async with client.test_factory() as s:
            topics_svc = TopicService(s)
            tasks_svc = TaskService(s)
            blocks_repo = BlockRepository(s)
            cards_repo = AcceptCardRepository(s)
            for i in range(4):
                topic = await topics_svc.create(
                    project_id=proj_uuid,
                    title=f"Channel {i}",
                    created_by="alice",
                )
                topic.environment = {"spec": "standard", "setup": "x" * 256}
                topic.default_environment = {"spec": "vm"}
                seeded_rooms.append(str(topic.id))

                root_msg = await blocks_repo.add(
                    project_id=proj_uuid,
                    conversation_id=topic.id,
                    author="alice",
                    author_type=AuthorType.participant,
                    content=f"主线消息 {i}",
                )
                thread = await open_thread(s, root_msg.id, by="alice")
                await blocks_repo.add(
                    project_id=proj_uuid,
                    conversation_id=thread.id,
                    author="bob",
                    author_type=AuthorType.participant,
                    content=f"回复 <@alice> {i}",
                    refs=["user:alice"],
                )

                mine_task = await tasks_svc.open_thread(
                    project_id=proj_uuid,
                    room_id=topic.id,
                    title=f"Alice task {i}",
                    owner_handle="alice",
                    created_by="alice",
                    contributor_handles=["carol"],
                )
                other_task = await tasks_svc.open_thread(
                    project_id=proj_uuid,
                    room_id=topic.id,
                    title=f"Bob task {i}",
                    owner_handle="bob",
                    created_by="bob",
                )
                for task in (mine_task, other_task):
                    await cards_repo.add(
                        topic_id=topic.id,
                        task_id=task.id,
                        reviewer_handles=["carol"],
                        status=AcceptStatus.pending,
                        change_subject=f"feat(test): deliver {task.id.hex[:8]}",
                    )
                await blocks_repo.add(
                    project_id=proj_uuid,
                    conversation_id=mine_task.id,
                    author="cheese",
                    author_type=AuthorType.participant,
                    content=f"问题 {i}",
                    meta={
                        "question": f"问题 {i}",
                        "options": [{"text": "A"}, {"text": "B"}],
                        "asked": ["alice"],
                    },
                    own_output=True,
                )
                await blocks_repo.add(
                    project_id=proj_uuid,
                    conversation_id=other_task.id,
                    author="bob",
                    author_type=AuthorType.participant,
                    content="进展正常",
                )
            await s.commit()
        return seeded_rooms

    return pid, client.portal.call(_seed, uuid.UUID(pid))


def _turn_running_elsewhere(client) -> None:
    """An open, delivered turn in another project's channel, as other tests (or
    other projects in production) leave behind: the sidebar reads every open
    turn site-wide, so this one must not cost the list a `tasks` lookup."""
    pid = post_project(client, json={"name": "Elsewhere"}, owner="bob").json()["data"][
        "id"
    ]
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "Busy"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200, r.text
    room = uuid.UUID(r.json()["data"]["id"])

    async def _insert() -> None:
        started = datetime.now(UTC) - timedelta(minutes=1)
        async with client.test_factory() as s:
            s.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=room,
                    continuation_id=uuid.uuid4(),
                    author="bob",
                    content="在别处跑",
                    is_resume=False,
                    resendable=True,
                    started_at=started,
                    delivered_at=started,
                    agent_handle="cheese-seat-elsewhere",
                )
            )
            await s.commit()

    client.portal.call(_insert)


def test_topic_list_batches_threads_cards_tasks_and_questions_once(client, monkeypatch):
    """Across a populated project, `GET /topics?project_id=` reads `topics`,
    `threads`, task `accept_cards`, underway `tasks`, and `awaiting_an_answer`
    `blocks` once each, while preserving the full response field set."""
    pid, room_ids = _seed_populated_project(client)
    _turn_running_elsewhere(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as seen:
        r = client.get(f"/topics?project_id={pid}", headers=headers)
    assert r.status_code == 200, r.text

    payload = r.json()["data"]
    items = payload["data"]
    assert payload["total"] == len(items) == 5  # 4 channels + 本体 root

    by_id = {row["id"]: row for row in items}
    for row in items:
        assert set(row.keys()) == EXPECTED_TOPIC_KEYS
        assert set(row["my_tasks"].keys()) == {"open", "shown"}

    for tid in room_ids:
        row = by_id[tid]
        assert row["awaits_me"] is True
        assert row["my_tasks"]["open"] == 2
        shown = row["my_tasks"]["shown"]
        assert len(shown) == 2
        own_row = next(t for t in shown if t["owner_handle"] == "alice")
        borrowed_row = next(t for t in shown if t["owner_handle"] == "bob")
        assert set(own_row.keys()) == EXPECTED_RAIL_TASK_KEYS
        assert set(borrowed_row.keys()) == EXPECTED_RAIL_TASK_KEYS | {"filling_in"}
        assert borrowed_row["filling_in"] is True
        assert own_row["card"] is not None
        assert borrowed_row["card"] is not None
        assert own_row["awaits_me"] is True

    # 1. `topics` is read once (no second COUNT(*) query) and defers sandbox JSON.
    assert _count(seen, _TOPICS_SELECT) == 1, [
        s for s in seen if _TOPICS_SELECT.search(s)
    ]
    topic_sql = next(s for s in seen if _TOPICS_SELECT.search(s))
    assert "topics.environment" not in topic_sql
    assert "topics.default_environment" not in topic_sql

    # 2. `threads` is read once across threads_of_rooms, replied_to_me, and
    #    threads_still_threads.
    assert _count(seen, _THREADS_SELECT) == 1, [
        s for s in seen if _THREADS_SELECT.search(s)
    ]

    # 3. `accept_cards` by task_id is read once for both mine and borrowed tasks.
    assert _count(seen, _TASK_CARDS_SELECT) == 1, [
        s for s in seen if _TASK_CARDS_SELECT.search(s)
    ]

    # 4. Question records are read once across the room list and all sidebar
    #    task rows. Later speech no longer determines whether they are settled.
    assert _count(seen, _AWAITING_PLACES_SELECT) == 1, [
        s for s in seen if _AWAITING_PLACES_SELECT.search(s)
    ]

    # 5. Underway `tasks` (tasks_of_rooms, underway_counts, underway_with,
    #    underway_others, last_block_at_for_tasks) is read once,
    #    and total `FROM tasks` queries are only 2 (_underway_for_rooms + unseen_deliveries).
    assert _count(seen, _UNDERWAY_TASKS_SELECT) == 1, [
        s for s in seen if _UNDERWAY_TASKS_SELECT.search(s)
    ]
    assert _count(seen, _ALL_TASKS_SELECT) == 2, [
        s for s in seen if _ALL_TASKS_SELECT.search(s)
    ]

    # 6. `projects`, `agent_instances`, `agent_bindings IN (...)`, and `session_states`
    #    are each read once across standing, roster, addressable_agent_handles, and rail_tasks.
    assert _count(seen, _PROJECTS_SELECT) == 1, [
        s for s in seen if _PROJECTS_SELECT.search(s)
    ]
    assert _count(seen, _AGENT_INSTANCES_SELECT) == 1, [
        s for s in seen if _AGENT_INSTANCES_SELECT.search(s)
    ]
    assert _count(seen, _AGENT_BINDINGS_IN_SELECT) == 1, [
        s for s in seen if _AGENT_BINDINGS_IN_SELECT.search(s)
    ]
    assert _count(seen, _SESSION_STATES_SELECT) == 1, [
        s for s in seen if _SESSION_STATES_SELECT.search(s)
    ]


def test_project_members_list_deduplicates_project_exclusions_and_counts(client):
    """`GET /projects/{id}/members` reads `projects`, `project_member_exclusions`,
    and `agent_instances` once each without issuing an unused `COUNT(*)` on
    `project_members`."""
    pid, _ = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as seen:
        r = client.get(f"/projects/{pid}/members", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["data"]
    assert len(items) >= 4

    assert _count(seen, _PROJECTS_SELECT) == 1, [
        s for s in seen if _PROJECTS_SELECT.search(s)
    ]
    assert _count(seen, _PROJECT_MEMBERS_COUNT_SELECT) == 0, [
        s for s in seen if _PROJECT_MEMBERS_COUNT_SELECT.search(s)
    ]
    assert _count(seen, _EXCLUSIONS_SELECT) == 1, [
        s for s in seen if _EXCLUSIONS_SELECT.search(s)
    ]
    assert _count(seen, _AGENT_INSTANCES_SELECT) == 1, [
        s for s in seen if _AGENT_INSTANCES_SELECT.search(s)
    ]


def test_my_teams_and_teams_list_batch_member_and_user_reads_once(client):
    """`GET /teams/my-teams` and `GET /teams` batch member relations and user
    profile/avatar lookups across all teams in one query per table instead of
    looping per team."""
    post_project(client, json={"name": "Seed user"}, owner="alice")
    headers = session_auth_headers("alice")
    for idx in range(3):
        resp = client.post(
            "/teams",
            json={
                "name": f"Batch Team {idx} {uuid.uuid4().hex[:6]}",
                "intro": "intro",
                "description": "desc",
                "avatarId": 1,
            },
            headers=headers,
        )
        assert resp.status_code in (200, 201), resp.text

    with counting_sql(client) as my_teams_sql:
        r_my = client.get("/teams/my-teams", headers=headers)
    assert r_my.status_code == 200, r_my.text
    assert len(r_my.json()["data"]["teams"]) >= 4
    assert _count(my_teams_sql, _TEAM_MEMBERS_PER_TEAM_SELECT) == 0, [
        s for s in my_teams_sql if _TEAM_MEMBERS_PER_TEAM_SELECT.search(s)
    ]
    assert _count(my_teams_sql, _USERS_IN_SELECT) == 1, [
        s for s in my_teams_sql if _USERS_IN_SELECT.search(s)
    ]

    with counting_sql(client) as teams_sql:
        r_all = client.get("/teams", headers=headers)
    assert r_all.status_code == 200, r_all.text
    assert len(r_all.json()["data"]["teams"]) >= 3
    assert _count(teams_sql, _TEAM_MEMBERS_PER_TEAM_SELECT) == 0, [
        s for s in teams_sql if _TEAM_MEMBERS_PER_TEAM_SELECT.search(s)
    ]
    assert _count(teams_sql, _USERS_IN_SELECT) == 1, [
        s for s in teams_sql if _USERS_IN_SELECT.search(s)
    ]


def test_session_cached_batches_invalidate_on_write(client):
    """Within one `AsyncSession`, repeated reads of `threads_of_rooms`,
    `threads_still_threads`, `tasks_of_rooms`, `underway_counts`,
    `underway_with`, `underway_others`, `last_block_at_for_tasks`, and
    `awaiting_an_answer` reuse the first answer with zero extra SQL, and a
    write in that session forgets the cached answer so the next read sees the
    new row."""
    pid = post_project(
        client, json={"name": "Cache invalidation"}, owner="alice"
    ).json()["data"]["id"]
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "Room"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    room_id = uuid.UUID(r.json()["data"]["id"])

    async def _exercise() -> tuple[int, int, int, int, int, int]:
        async with client.test_factory() as s:
            sent: list[str] = []

            def _record(_conn, _cursor, statement, _params, _ctx, _many):
                sent.append(statement)

            engine = s.get_bind()
            engine = getattr(engine, "sync_engine", engine)
            event.listen(engine, "before_cursor_execute", _record)
            try:
                assert await threads_of_rooms(s, [room_id]) == {}
                assert await TaskService(s).tasks_of_rooms([room_id]) == {}
                assert await BlockRepository(s).awaiting_an_answer([room_id]) == {}
                after_initial = len(sent)

                # Repeated reads in the same transaction emit zero extra SQL.
                assert await threads_still_threads(s, [room_id]) == {}
                assert await TaskService(s).underway_counts([room_id]) == {}
                assert await TaskService(s).underway_with([room_id], "alice") == []
                assert await TaskService(s).underway_others([room_id], "alice") == []
                assert await BlockRepository(s).awaiting_an_answer([room_id]) == {}
                extra_before_write = len(sent) - after_initial

                # A write on the session must invalidate the remembered facts.
                task = await TaskService(s).open_thread(
                    project_id=uuid.UUID(pid),
                    room_id=room_id,
                    title="New task",
                    owner_handle="alice",
                    created_by="alice",
                )
                tasks_after = await TaskService(s).tasks_of_rooms([room_id])
                after_repopulate = len(sent)
                counts_after = await TaskService(s).underway_counts([room_id])
                mine_after = await TaskService(s).underway_with([room_id], "alice")
                others_after = await TaskService(s).underway_others([room_id], "alice")
                last_after = await TaskService(s).last_block_at_for_tasks([task.id])
                extra_after_repopulate = len(sent) - after_repopulate
                assert last_after == {}
            finally:
                event.remove(engine, "before_cursor_execute", _record)
            await s.rollback()
            return (
                extra_before_write,
                extra_after_repopulate,
                len(tasks_after),
                counts_after.get(room_id, 0),
                len(mine_after),
                len(others_after),
            )

    assert client.portal.call(_exercise) == (0, 0, 1, 1, 1, 0)


def test_topic_names_list_defers_sandbox_environment_columns(client):
    """`GET /topics/names` (`reads.names_in_projects`) defers `environment` and
    `default_environment` while returning the same topic fields."""
    pid = post_project(client, json={"name": "Names project"}, owner="alice").json()[
        "data"
    ]["id"]
    headers = session_auth_headers("alice")
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "Named channel"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    tid = r.json()["data"]["id"]

    with counting_sql(client) as seen:
        names_resp = client.get("/topics/names", headers=headers)
    assert names_resp.status_code == 200, names_resp.text

    topics = names_resp.json()["data"]["topics"]
    matched = next(t for t in topics if t["id"] == tid)
    assert set(matched.keys()) == {
        "id",
        "project_id",
        "title",
        "kind",
        "status",
        "members_only",
    }
    topic_selects = [s for s in seen if _TOPICS_SELECT.search(s)]
    assert len(topic_selects) == 1
    assert "topics.environment" not in topic_selects[0]
    assert "topics.default_environment" not in topic_selects[0]


def test_every_room_row_names_the_seat_its_teammate_sits_in(client):
    """A row's state can name the room's teammate (「{agent}在做」), and a project
    may have renamed it, so the row says which seat that is — the one a message
    nobody addressed goes to — and the list and the header agree on it."""
    headers = session_auth_headers("alice")
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    rooms = [
        client.post(
            "/topics", json={"project_id": pid, "title": title}, headers=headers
        ).json()["data"]["id"]
        for title in ("甲", "乙")
    ]

    listed = {
        row["id"]: row["agent_handle"]
        for row in client.get(f"/topics?project_id={pid}", headers=headers).json()[
            "data"
        ]["data"]
    }
    for room in rooms:
        seat = room_agent_seat(client, room)
        assert listed[room] == seat
        header = client.get(f"/topics/{room}", headers=headers).json()["data"]
        assert header["agent_handle"] == seat


def test_user_profile_deduplicates_self_teams_and_empty_understanding_queries(client):
    """`GET /users/{handle}/profile` when viewing one's own profile queries
    `team_user_relation` once instead of twice for the same user, and skips
    empty `IN (NULL)` queries when `_understanding` finds no memory entries."""
    post_project(client, json={"name": "Profile project"}, owner="alice")
    headers = session_auth_headers("alice")

    with counting_sql(client) as seen:
        r = client.get("/users/alice/profile", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["handle"] == "alice"

    assert _count(seen, _TEAM_USER_REL_FOR_USER_SELECT) == 1, [
        s for s in seen if _TEAM_USER_REL_FOR_USER_SELECT.search(s)
    ]
    assert _count(seen, _EMPTY_IN_NULL_SELECT) == 0, [
        s for s in seen if _EMPTY_IN_NULL_SELECT.search(s)
    ]


def test_task_header_deduplicates_card_running_and_room_of_queries(client):
    """`GET /topics/{task_id}/task` on a task with no acceptance card queries
    `accept_cards` once, `session_states` once, and reuses the room resolved by
    `place_or_404` without issuing a second `room_of` UNION query."""
    headers = session_auth_headers("alice")
    pid = post_project(client, json={"name": "Task header"}, owner="alice").json()[
        "data"
    ]["id"]
    room_id = client.post(
        "/topics", json={"project_id": pid, "title": "Main"}, headers=headers
    ).json()["data"]["id"]
    task_id = client.post(
        f"/topics/{room_id}/tasks", json={"title": "Cardless task"}, headers=headers
    ).json()["data"]["id"]

    with counting_sql(client) as seen:
        r = client.get(f"/topics/{task_id}/task", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["id"] == task_id

    assert _count(seen, _TASK_CARDS_SELECT) == 1, [
        s for s in seen if _TASK_CARDS_SELECT.search(s)
    ]
    assert _count(seen, _SESSION_STATES_SELECT) == 1, [
        s for s in seen if _SESSION_STATES_SELECT.search(s)
    ]
    assert _count(seen, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in seen if _ROOM_OF_UNION_SELECT.search(s)
    ]


def test_channel_directory_deduplicates_topic_memberships_and_fallback_reads(client):
    """`GET /projects/{id}/channels` reads `topic_memberships` once across
    `owners_of`, `seen`, `joined_topic_ids`, `seat_counts`, and
    `managed_topic_ids`, and reads `topics` once without a second `get_many`
    query on the root topic."""
    pid, _ = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as seen:
        r = client.get(f"/projects/{pid}/channels", headers=headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["items"]) == 5

    assert _count(seen, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in seen if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert _count(seen, _TOPICS_SELECT) == 2, [
        s for s in seen if _TOPICS_SELECT.search(s)
    ]


def test_topic_members_deduplicates_room_of_profiles_bindings_and_instances(client):
    """`GET /topics/{room_id}/members` reuses the resolved room without a
    `room_of` UNION query, reads `user_profile` once via
    `nickname_and_avatar_by_user_id`, and queries `topic_memberships`,
    `agent_bindings IN (...)`, and `agent_instances` once each."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    with counting_sql(client) as seen:
        r = client.get(f"/topics/{room_id}/members", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["data"]
    assert len(items) >= 2

    assert _count(seen, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in seen if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert _count(seen, _USER_PROFILE_SELECT) == 1, [
        s for s in seen if _USER_PROFILE_SELECT.search(s)
    ]
    assert _count(seen, _AGENT_BINDINGS_IN_SELECT) == 1, [
        s for s in seen if _AGENT_BINDINGS_IN_SELECT.search(s)
    ]
    assert _count(seen, _AGENT_INSTANCES_SELECT) == 1, [
        s for s in seen if _AGENT_INSTANCES_SELECT.search(s)
    ]
    assert len(seen) <= 9, f"expected <= 9 SQL statements, got {len(seen)}: {seen}"


def test_topic_list_and_room_header_deduplicate_memberships_and_failed_turns(client):
    """`GET /topics?project_id=` and `GET /topics/{room_id}` each query
    `topic_memberships` once (reusing cached pairs for `joined_topic_ids` and
    `managed_topic_ids`) and query `FAILED_TURN_ROWS` blocks once across
    `_unresolved_failures` and `MemberWaits.for_rooms`."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    with counting_sql(client) as list_sql:
        r_list = client.get(f"/topics?project_id={pid}", headers=headers)
    assert r_list.status_code == 200, r_list.text
    assert _count(list_sql, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in list_sql if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert _count(list_sql, _FAILED_TURN_BLOCKS_SELECT) == 1, [
        s for s in list_sql if _FAILED_TURN_BLOCKS_SELECT.search(s)
    ]

    with counting_sql(client) as room_sql:
        r_room = client.get(f"/topics/{room_id}", headers=headers)
    assert r_room.status_code == 200, r_room.text
    assert _count(room_sql, _FAILED_TURN_BLOCKS_SELECT) == 1, [
        s for s in room_sql if _FAILED_TURN_BLOCKS_SELECT.search(s)
    ]
    assert len(room_sql) <= 23, (
        f"expected <= 23 SQL statements on room header, got {len(room_sql)}: {room_sql}"
    )


def test_room_tasks_and_accept_cards_deduplicate_room_cards_and_forges(client):
    """`GET /topics/{room_id}/tasks` avoids the `room_of` UNION query and
    reuses `latest_and_counts_by_task`'s cards in `accepted_for_tasks`, while
    `GET /topics/{task_id}/accept-card` reuses `PlaceResolver`'s room/task rows
    and queries `project_forges` once."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    with counting_sql(client) as tasks_sql:
        r_tasks = client.get(f"/topics/{room_id}/tasks?status=open", headers=headers)
    assert r_tasks.status_code == 200, r_tasks.text
    tasks_data = r_tasks.json()["data"]["data"]
    assert len(tasks_data) == 2
    task_id = tasks_data[0]["id"]

    assert _count(tasks_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in tasks_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert _count(tasks_sql, _ACCEPT_CARDS_ALL_SELECT) == 1, [
        s for s in tasks_sql if _ACCEPT_CARDS_ALL_SELECT.search(s)
    ]

    with counting_sql(client) as cards_sql:
        r_cards = client.get(f"/topics/{task_id}/accept-card", headers=headers)
    assert r_cards.status_code == 200, r_cards.text
    assert len(r_cards.json()["data"]["data"]) == 1

    assert _count(cards_sql, _ALL_TASKS_SELECT) == 1, [
        s for s in cards_sql if _ALL_TASKS_SELECT.search(s)
    ]
    assert _count(cards_sql, _PROJECT_FORGES_SELECT) == 1, [
        s for s in cards_sql if _PROJECT_FORGES_SELECT.search(s)
    ]
    assert len(cards_sql) <= 10, (
        f"expected <= 10 SQL statements on accept-card list, got {len(cards_sql)}: {cards_sql}"
    )


def test_room_blocks_deduplicates_root_messages_and_routine_runs(client):
    """`GET /topics/{room_id}/blocks?limit=25` reuses the page's loaded `Block`
    rows when describing thread roots instead of re-querying `blocks.id = ANY(...)`,
    and queries `routine_runs JOIN routines` once across `runs_under` and
    `routine_runs_of`."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    with counting_sql(client) as seen:
        r = client.get(f"/topics/{room_id}/blocks?limit=25", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["data"]
    assert len(items) >= 1
    assert any("thread" in item for item in items)

    assert _count(seen, _BLOCKS_BY_IDS_SELECT) == 0, [
        s for s in seen if _BLOCKS_BY_IDS_SELECT.search(s)
    ]
    assert _count(seen, _ROUTINE_RUNS_SELECT) == 1, [
        s for s in seen if _ROUTINE_RUNS_SELECT.search(s)
    ]
    # 14 since the page reads who is answering in each 支线 from session_states
    # (one read of the running sessions) instead of the realtime process's
    # broker, so any backend process can serve it (`core/process_role.py`).
    assert len(seen) <= 14, f"expected <= 14 SQL statements, got {len(seen)}: {seen}"


def test_awaiting_me_and_project_tasks_deduplicate_reviewers_and_last_said(client):
    """`GET /awaiting-me` queries `accept_card_reviewer` once across
    `listed_cards` and `waiting_items`, while `GET /projects/{id}/tasks` reuses
    `page_for_project`'s `moved` timestamp without a second `last_block_at_for_tasks`
    query."""
    pid, _ = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as awaiting_sql:
        r_await = client.get("/awaiting-me", headers=headers)
    assert r_await.status_code == 200, r_await.text
    assert _count(awaiting_sql, _ACCEPT_CARD_REVIEWER_SELECT) == 1, [
        s for s in awaiting_sql if _ACCEPT_CARD_REVIEWER_SELECT.search(s)
    ]

    with counting_sql(client) as tasks_sql:
        r_tasks = client.get(
            f"/projects/{pid}/tasks?status=open&limit=200", headers=headers
        )
    assert r_tasks.status_code == 200, r_tasks.text
    assert len(r_tasks.json()["data"]["data"]) == 8
    assert _count(tasks_sql, _TASK_LAST_SAID_SELECT) == 0, [
        s for s in tasks_sql if _TASK_LAST_SAID_SELECT.search(s)
    ]
    assert len(tasks_sql) <= 12, (
        f"expected <= 12 SQL statements on project tasks, got {len(tasks_sql)}: {tasks_sql}"
    )


def test_project_agents_and_room_endpoints_deduplicate_instances_and_memberships(
    client,
):
    """`GET /projects/{id}/agents` queries `agent_instances` once, and
    `GET /topics/{room_id}` and `GET /topics/{room_id}/members` each query
    `topic_memberships` once across authorization and roster assembly."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    with counting_sql(client) as agents_sql:
        r_agents = client.get(f"/projects/{pid}/agents", headers=headers)
    assert r_agents.status_code == 200, r_agents.text
    assert len(r_agents.json()["data"]["data"]) >= 1
    assert _count(agents_sql, _AGENT_INSTANCES_SELECT) == 1, [
        s for s in agents_sql if _AGENT_INSTANCES_SELECT.search(s)
    ]
    assert len(agents_sql) <= 5, (
        f"expected <= 5 SQL statements on project agents, got {len(agents_sql)}: {agents_sql}"
    )

    with counting_sql(client) as room_sql:
        r_room = client.get(f"/topics/{room_id}", headers=headers)
    assert r_room.status_code == 200, r_room.text
    assert _count(room_sql, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in room_sql if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert len(room_sql) <= 22, (
        f"expected <= 22 SQL statements on room header, got {len(room_sql)}: {room_sql}"
    )

    with counting_sql(client) as members_sql:
        r_members = client.get(f"/topics/{room_id}/members", headers=headers)
    assert r_members.status_code == 200, r_members.text
    assert _count(members_sql, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in members_sql if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert len(members_sql) <= 8, (
        f"expected <= 8 SQL statements on room members, got {len(members_sql)}: {members_sql}"
    )


def test_room_pins_chapters_and_proposals_pass_room_to_authorize_topic(client):
    """`GET /topics/{room_id}/pins`, `GET /topics/{room_id}/chapters`, and
    `GET /topics/{room_id}/feedback-proposals` pass `room=place.room` to
    `authorize_topic` so none of them issues a redundant `room_of` UNION query."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    for suffix, max_sql in (("pins", 4), ("chapters", 4), ("feedback-proposals", 5)):
        with counting_sql(client) as seen:
            r = client.get(f"/topics/{room_id}/{suffix}", headers=headers)
        assert r.status_code == 200, r.text
        assert _count(seen, _ROOM_OF_UNION_SELECT) == 0, [
            s for s in seen if _ROOM_OF_UNION_SELECT.search(s)
        ]
        assert len(seen) <= max_sql, (
            f"expected <= {max_sql} SQL statements on {suffix}, got {len(seen)}: {seen}"
        )


def test_project_artifacts_and_channels_unseen_skip_redundant_topic_queries(client):
    """`GET /projects/{id}/artifacts` queries `topics` once when no private
    channels exist, avoiding a second `ids_for_project` round-trip."""
    pid, _ = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as seen:
        r = client.get(f"/projects/{pid}/artifacts", headers=headers)
    assert r.status_code == 200, r.text
    assert _count(seen, _TOPICS_SELECT) == 1, [
        s for s in seen if _TOPICS_SELECT.search(s)
    ]
    assert len(seen) <= 6, (
        f"expected <= 6 SQL statements on project artifacts, got {len(seen)}: {seen}"
    )


def test_project_roles_deduplicates_role_defs_query(client):
    """`GET /projects/{id}/roles` queries `project_role_defs` once across
    `RoleService.holders` and `RoleService.roles`."""
    pid, _ = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as seen:
        r = client.get(f"/projects/{pid}/roles", headers=headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["roles"]) >= 1
    assert _count(seen, _PROJECT_ROLE_DEFS_SELECT) == 1, [
        s for s in seen if _PROJECT_ROLE_DEFS_SELECT.search(s)
    ]
    assert len(seen) <= 14, (
        f"expected <= 14 SQL statements on project roles, got {len(seen)}: {seen}"
    )


def test_send_chat_message_deduplicates_archived_project_and_post_commit_roster_reads(
    client,
):
    """`POST /topics/{room_id}/messages` reads `projects` once across
    `refuse_writes_if_archived` and `may`, and resolves `people` before the
    writer session commits so `topic_memberships` is read once."""
    pid, room_ids = _seed_populated_project(client)
    room_id = room_ids[0]

    with counting_sql(client) as seen:
        sent = post_message(client, room_id, "alice", {"content": "测试发消息去重"})
    assert sent["content"] == "测试发消息去重"

    assert _count(seen, _PROJECTS_SELECT) == 1, [
        s for s in seen if _PROJECTS_SELECT.search(s)
    ]
    assert _count(seen, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in seen if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert len(seen) <= 15, (
        f"expected <= 15 SQL statements on send_chat_message, got {len(seen)}: {seen}"
    )


def test_team_by_handle_and_my_teams_deduplicate_profiles_and_join_status(client):
    """`GET /teams/by-handle/{handle}`, `GET /teams/my-teams`, and `GET /teams`
    each query `user_profile` once via `profiles_and_chosen_avatars`, and
    `GET /teams/by-handle/{handle}` skips redundant `get_by_handle` and
    `join_status` queries for a member's own team."""
    post_project(client, json={"name": "Team profile seed"}, owner="alice")
    headers = session_auth_headers("alice")

    with counting_sql(client) as handle_sql:
        r_handle = client.get("/teams/by-handle/alice", headers=headers)
    assert r_handle.status_code == 200, r_handle.text
    assert r_handle.json()["data"]["team"]["joinStatus"] == "member"
    assert _count(handle_sql, _USER_PROFILE_SELECT) == 1, [
        s for s in handle_sql if _USER_PROFILE_SELECT.search(s)
    ]
    assert len(handle_sql) <= 5, (
        f"expected <= 5 SQL statements on /teams/by-handle/alice, got {len(handle_sql)}: {handle_sql}"
    )

    with counting_sql(client) as my_sql:
        r_my = client.get("/teams/my-teams", headers=headers)
    assert r_my.status_code == 200, r_my.text
    assert _count(my_sql, _USER_PROFILE_SELECT) == 1, [
        s for s in my_sql if _USER_PROFILE_SELECT.search(s)
    ]


def test_room_snapshot_batches_open_and_recent_closed_tasks_in_one_pass(client):
    """`room_snapshot` on a channel with both open and closed tasks hydrates
    both lists in a single `task_rows` + `with_session_running` pass instead of
    querying `accept_cards` and `awaiting_an_answer` twice."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]
    tasks = client.get(f"/topics/{room_id}/tasks?status=open", headers=headers).json()[
        "data"
    ]["data"]
    closed_id = tasks[0]["id"]
    r_close = client.post(
        f"/topics/{closed_id}/close", json={"conclusion": "done"}, headers=headers
    )
    assert r_close.status_code == 200, r_close.text

    snap_sql: list[str] = []

    async def _run():
        async with client.test_factory() as s:
            eng = s.get_bind()
            eng = getattr(eng, "sync_engine", eng)

            def _rec(_conn, _cur, stmt, _p, _ctx, _m):
                st = " ".join(stmt.split())
                if not st.startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
                    snap_sql.append(st)

            event.listen(eng, "before_cursor_execute", _rec)
            try:
                return await room_snapshot(
                    s,
                    Actor(handle="alice", user_id=2, via="token"),
                    uuid.UUID(room_id),
                )
            finally:
                event.remove(eng, "before_cursor_execute", _rec)

    snap = client.portal.call(_run)
    assert snap is not None
    assert len(snap["tasks"]["open"]) == 1
    assert len(snap["tasks"]["recent"]) == 1
    assert _count(snap_sql, _TASK_CARDS_SELECT) == 1, [
        s for s in snap_sql if _TASK_CARDS_SELECT.search(s)
    ]
    assert _count(snap_sql, _AWAITING_PLACES_SELECT) == 1, [
        s for s in snap_sql if _AWAITING_PLACES_SELECT.search(s)
    ]


_PLANS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM plans\b",
    re.IGNORECASE,
)
_COMPUTE_GRANTS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM compute_grants\b",
    re.IGNORECASE,
)
_FEEDBACK_DISMISSALS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM feedback_proposal_dismissals\b",
    re.IGNORECASE,
)


def test_topic_status_and_feedback_proposals_avoid_redundant_credit_and_dismissal_queries(
    client,
):
    """`GET /topics/{room_id}/status` reads `plans` and `compute_grants` once
    via `UsageService.project_status_credits`, and `GET /topics/{room_id}/feedback-proposals`
    skips `feedback_proposal_dismissals` when the room has no proposal blocks."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    with counting_sql(client) as status_sql:
        r_status = client.get(f"/topics/{room_id}/status", headers=headers)
    assert r_status.status_code == 200, r_status.text
    assert _count(status_sql, _PLANS_SELECT) == 1, [
        s for s in status_sql if _PLANS_SELECT.search(s)
    ]
    assert _count(status_sql, _COMPUTE_GRANTS_SELECT) == 1, [
        s for s in status_sql if _COMPUTE_GRANTS_SELECT.search(s)
    ]
    assert len(status_sql) <= 12, (
        f"expected <= 12 SQL statements on /status, got {len(status_sql)}: {status_sql}"
    )

    with counting_sql(client) as prop_sql:
        r_prop = client.get(f"/topics/{room_id}/feedback-proposals", headers=headers)
    assert r_prop.status_code == 200, r_prop.text
    assert _count(prop_sql, _FEEDBACK_DISMISSALS_SELECT) == 0, [
        s for s in prop_sql if _FEEDBACK_DISMISSALS_SELECT.search(s)
    ]
    assert len(prop_sql) <= 4, (
        f"expected <= 4 SQL statements on /feedback-proposals, got {len(prop_sql)}: {prop_sql}"
    )


def test_write_progress_and_addressing_reuse_loaded_room_and_skip_no_mention_roster(
    client,
):
    """`PUT /topics/{room_id}/progress` without `@` mentions and
    `POST /topics/{room_id}/messages/addressing` reuse the already-loaded
    `place.room`, and `PUT /topics/{room_id}/progress` skips loading the
    project roster when the checklist text has no `@`."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    room_id = room_ids[0]

    with counting_sql(client) as addr_sql:
        r_addr = client.post(
            f"/topics/{room_id}/messages/addressing",
            json={"content": "hello @cheese", "with_text": True},
            headers=headers,
        )
    assert r_addr.status_code == 200, r_addr.text
    assert _count(addr_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in addr_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]

    with counting_sql(client) as prog_sql:
        r_prog = client.put(
            f"/topics/{room_id}/progress",
            json={"todos": [{"content": "step 1", "status": "in_progress"}]},
            headers=headers,
        )
    assert r_prog.status_code == 200, r_prog.text
    assert _count(prog_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in prog_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert _count(prog_sql, _TOPICS_SELECT) == 1, [
        s for s in prog_sql if _TOPICS_SELECT.search(s)
    ]
    assert _count(prog_sql, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in prog_sql if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert len(prog_sql) <= 11, (
        f"expected <= 11 SQL statements on PUT /progress, got {len(prog_sql)}: {prog_sql}"
    )


def test_get_team_and_team_members_deduplicate_team_user_relation_queries(client):
    """`GET /teams/{team_id}` and `GET /teams/{team_id}/members` each query
    `team_user_relation` once instead of running a separate membership/seat
    query before listing the team's members."""
    _pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    my_teams = client.get("/teams/my-teams", headers=headers).json()["data"]["teams"]
    team_id = next(t["id"] for t in my_teams if not t["personal"])

    with counting_sql(client) as team_sql:
        r_team = client.get(f"/teams/{team_id}", headers=headers)
    assert r_team.status_code == 200, r_team.text
    assert _count(team_sql, _TEAM_MEMBERS_PER_TEAM_SELECT) == 1, [
        s for s in team_sql if _TEAM_MEMBERS_PER_TEAM_SELECT.search(s)
    ]
    assert len(team_sql) <= 5, (
        f"expected <= 5 SQL statements on GET /teams/{{id}}, got {len(team_sql)}: {team_sql}"
    )

    with counting_sql(client) as mem_sql:
        r_mem = client.get(f"/teams/{team_id}/members", headers=headers)
    assert r_mem.status_code == 200, r_mem.text
    assert _count(mem_sql, _TEAM_MEMBERS_PER_TEAM_SELECT) == 1, [
        s for s in mem_sql if _TEAM_MEMBERS_PER_TEAM_SELECT.search(s)
    ]
    assert len(mem_sql) <= 5, (
        f"expected <= 5 SQL statements on GET /teams/{{id}}/members, got {len(mem_sql)}: {mem_sql}"
    )


_THREADS_IN_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM threads\s+WHERE threads\.id IN\b",
    re.IGNORECASE,
)
_CONVERSATIONS_KIND_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM conversations\s+WHERE conversations\.id =",
    re.IGNORECASE,
)
_TOPIC_MEMBERSHIPS_COUNT_SELECT = re.compile(
    r"^\s*SELECT count\(\*\)\b[^;]*?\bFROM topic_memberships\b",
    re.IGNORECASE,
)


def test_document_and_overview_routes_avoid_redundant_task_and_room_queries(client):
    """`GET /topics/{task_id}/document`, `GET /documents/{doc_id}/about`, and
    `GET /projects/{project_id}/overview` reuse already-loaded task, room, and
    overview document pointers instead of re-querying `topics`, `tasks`, or
    `projects.overview_document_id`."""
    pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    tasks = client.get(f"/topics/{ch}/tasks?status=open", headers=headers).json()[
        "data"
    ]["data"]
    task_id = tasks[0]["id"]
    client.get(f"/projects/{pid}/overview", headers=headers)
    tdoc = client.get(f"/topics/{task_id}/document", headers=headers).json()["data"]
    tdoc_id = tdoc["id"]

    with counting_sql(client) as ov_sql:
        r_ov = client.get(f"/projects/{pid}/overview", headers=headers)
    assert r_ov.status_code == 200, r_ov.text
    assert _count(ov_sql, _PROJECTS_SELECT) == 1, [
        s for s in ov_sql if _PROJECTS_SELECT.search(s)
    ]
    assert len(ov_sql) <= 5, (
        f"expected <= 5 SQL statements on GET /projects/{{id}}/overview, got {len(ov_sql)}: {ov_sql}"
    )

    with counting_sql(client) as tdoc_sql:
        r_tdoc = client.get(f"/topics/{task_id}/document", headers=headers)
    assert r_tdoc.status_code == 200, r_tdoc.text
    assert _count(tdoc_sql, _TOPICS_SELECT) == 1, [
        s for s in tdoc_sql if _TOPICS_SELECT.search(s)
    ]
    assert _count(tdoc_sql, _ALL_TASKS_SELECT) == 1, [
        s for s in tdoc_sql if _ALL_TASKS_SELECT.search(s)
    ]
    assert len(tdoc_sql) <= 4, (
        f"expected <= 4 SQL statements on GET /topics/{{task}}/document, got {len(tdoc_sql)}: {tdoc_sql}"
    )

    with counting_sql(client) as about_sql:
        r_about = client.get(f"/documents/{tdoc_id}/about", headers=headers)
    assert r_about.status_code == 200, r_about.text
    assert _count(about_sql, _TOPICS_SELECT) == 1, [
        s for s in about_sql if _TOPICS_SELECT.search(s)
    ]
    assert _count(about_sql, _ALL_TASKS_SELECT) == 1, [
        s for s in about_sql if _ALL_TASKS_SELECT.search(s)
    ]
    assert _count(about_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in about_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert len(about_sql) <= 5, (
        f"expected <= 5 SQL statements on GET /documents/{{id}}/about, got {len(about_sql)}: {about_sql}"
    )


def test_task_detail_and_task_list_skip_redundant_topic_and_thread_probes(client):
    """`GET /topics/{task_id}/task` resolves the task directly without probing
    `topics` for `task_id`, and both `GET /topics/{task_id}/task` and
    `GET /topics/{room_id}/tasks` skip `SELECT threads.id` when no task has
    `upgraded_from_block_id` set."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    tasks = client.get(f"/topics/{ch}/tasks?status=open", headers=headers).json()[
        "data"
    ]["data"]
    task_id = tasks[0]["id"]

    with counting_sql(client) as task_sql:
        r_task = client.get(f"/topics/{task_id}/task", headers=headers)
    assert r_task.status_code == 200, r_task.text
    assert _count(task_sql, _TOPICS_SELECT) == 1, [
        s for s in task_sql if _TOPICS_SELECT.search(s)
    ]
    assert _count(task_sql, _THREADS_IN_SELECT) == 0, [
        s for s in task_sql if _THREADS_IN_SELECT.search(s)
    ]
    assert len(task_sql) <= 11, (
        f"expected <= 11 SQL statements on GET /topics/{{task}}/task, got {len(task_sql)}: {task_sql}"
    )

    with counting_sql(client) as list_sql:
        r_list = client.get(f"/topics/{ch}/tasks?status=open", headers=headers)
    assert r_list.status_code == 200, r_list.text
    assert _count(list_sql, _THREADS_IN_SELECT) == 0, [
        s for s in list_sql if _THREADS_IN_SELECT.search(s)
    ]


def test_edit_block_deduplicates_membership_count_agent_lookup_and_conversation_kind(
    client,
):
    """`PATCH /blocks/{block_id}` without `@` mentions reuses cached
    `topic_memberships` for both `roles_for_member` and `count_for_topic`,
    skips `_agent_at`, and resolves `is_task` before flushing `replace_content`."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    msg = post_message(client, ch, "alice", {"content": "initial message"})
    msg_id = msg["id"]

    with counting_sql(client) as edit_sql:
        r_edit = client.patch(
            f"/blocks/{msg_id}",
            json={"content": "updated message without mention"},
            headers=headers,
        )
    assert r_edit.status_code == 200, r_edit.text
    assert _count(edit_sql, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in edit_sql if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert _count(edit_sql, _TOPIC_MEMBERSHIPS_COUNT_SELECT) == 0, [
        s for s in edit_sql if _TOPIC_MEMBERSHIPS_COUNT_SELECT.search(s)
    ]
    assert _count(edit_sql, _AGENT_INSTANCES_SELECT) == 0, [
        s for s in edit_sql if _AGENT_INSTANCES_SELECT.search(s)
    ]
    assert _count(edit_sql, _CONVERSATIONS_KIND_SELECT) == 0, [
        s for s in edit_sql if _CONVERSATIONS_KIND_SELECT.search(s)
    ]
    assert len(edit_sql) <= 10, (
        f"expected <= 10 SQL statements on PATCH /blocks/{{id}}, got {len(edit_sql)}: {edit_sql}"
    )


_DOC_NODES_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM document_nodes\b",
    re.IGNORECASE,
)
_DOC_LINKS_DELETE = re.compile(
    r"^\s*DELETE\s+FROM\s+document_links\b",
    re.IGNORECASE,
)
_PROJECT_MEMBERS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM project_members\b",
    re.IGNORECASE,
)
_TEAM_USER_REL_JOIN_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bJOIN team_user_relation\b",
    re.IGNORECASE,
)
_ACCEPT_CARDS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM accept_cards\b",
    re.IGNORECASE,
)


def test_create_project_document_skips_redundant_team_task_node_and_link_queries(
    client,
):
    """`POST /projects/{project_id}/documents` reuses the cached `Project` for
    `team_id`, skips `whose_document` on numbered project documents, avoids an
    extra `SELECT documents.*` refresh after `set_content`, and skips querying
    `document_nodes` or deleting `document_links` on a brand-new link-free doc."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as doc_sql:
        r_doc = client.post(
            f"/projects/{pid}/documents",
            json={"title": "Spec Doc", "content": "plain markdown body"},
            headers=headers,
        )
    assert r_doc.status_code == 200, r_doc.text
    assert _count(doc_sql, _PROJECTS_SELECT) == 1, [
        s for s in doc_sql if _PROJECTS_SELECT.search(s)
    ]
    assert _count(doc_sql, _ALL_TASKS_SELECT) == 0, [
        s for s in doc_sql if _ALL_TASKS_SELECT.search(s)
    ]
    assert _count(doc_sql, _DOC_NODES_SELECT) == 0, [
        s for s in doc_sql if _DOC_NODES_SELECT.search(s)
    ]
    assert _count(doc_sql, _DOC_LINKS_DELETE) == 0, [
        s for s in doc_sql if _DOC_LINKS_DELETE.search(s)
    ]
    assert len(doc_sql) <= 19, (
        f"expected <= 19 SQL statements on POST /projects/{{id}}/documents, got {len(doc_sql)}: {doc_sql}"
    )


def test_list_project_members_deduplicates_project_members_owner_profile_and_team_roles(
    client,
):
    """`GET /projects/{project_id}/members` reads `project_members` and
    `"user" JOIN team_user_relation` once each and resolves the owner's profile
    from the loaded team/member rows instead of issuing a separate query."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as mem_sql:
        r_mem = client.get(f"/projects/{pid}/members", headers=headers)
    assert r_mem.status_code == 200, r_mem.text
    assert _count(mem_sql, _PROJECT_MEMBERS_SELECT) == 1, [
        s for s in mem_sql if _PROJECT_MEMBERS_SELECT.search(s)
    ]
    assert _count(mem_sql, _TEAM_USER_REL_JOIN_SELECT) == 1, [
        s for s in mem_sql if _TEAM_USER_REL_JOIN_SELECT.search(s)
    ]
    assert len(mem_sql) <= 15, (
        f"expected <= 15 SQL statements on GET /projects/{{id}}/members, got {len(mem_sql)}: {mem_sql}"
    )


def test_task_close_reopen_and_topic_children_avoid_redundant_lock_and_card_queries(
    client,
):
    """`POST /topics/{task}/close`, `POST /topics/{task}/reopen`, and
    `GET /topics/{id}/children` reuse identity-mapped task/room rows across
    `lock_task_for_change` and `announce`, and `conclude_task` reads
    `accept_cards` once."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    tasks = client.get(f"/topics/{ch}/tasks?status=open", headers=headers).json()[
        "data"
    ]["data"]
    task_id = tasks[0]["id"]

    with counting_sql(client) as close_sql:
        r_close = client.post(
            f"/topics/{task_id}/close",
            json={"conclusion": "done"},
            headers=headers,
        )
    assert r_close.status_code == 200, r_close.text
    assert _count(close_sql, _TOPICS_SELECT) == 1, [
        s for s in close_sql if _TOPICS_SELECT.search(s)
    ]
    assert _count(close_sql, _ACCEPT_CARDS_SELECT) == 1, [
        s for s in close_sql if _ACCEPT_CARDS_SELECT.search(s)
    ]
    assert len(close_sql) <= 18, (
        f"expected <= 18 SQL statements on POST /topics/{{task}}/close, got {len(close_sql)}: {close_sql}"
    )

    with counting_sql(client) as reopen_sql:
        r_reopen = client.post(
            f"/topics/{task_id}/reopen",
            json={},
            headers=headers,
        )
    assert r_reopen.status_code == 200, r_reopen.text
    assert len(reopen_sql) <= 17, (
        f"expected <= 17 SQL statements on POST /topics/{{task}}/reopen, got {len(reopen_sql)}: {reopen_sql}"
    )

    with counting_sql(client) as child_sql:
        r_child = client.get(f"/topics/{ch}/children", headers=headers)
    assert r_child.status_code == 200, r_child.text
    assert _count(child_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in child_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert len(child_sql) <= 4, (
        f"expected <= 4 SQL statements on GET /topics/{{id}}/children, got {len(child_sql)}: {child_sql}"
    )


_NOTIFICATION_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM notification\b",
    re.IGNORECASE,
)


def test_add_document_comment_without_mention_skips_project_roster_and_seat_lookup(
    client,
):
    """`POST /documents/{id}/comments` without `@` mentions skips loading the
    project roster (`project_members`, `team_user_relation`, `agent_instances`)
    and skips querying `question.seats`."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    doc = client.post(
        f"/projects/{pid}/documents",
        json={"title": "Doc 1", "content": "hello world"},
        headers=headers,
    ).json()["data"]
    doc_id = doc["id"]

    with counting_sql(client) as c_sql:
        r_c = client.post(
            f"/documents/{doc_id}/comments",
            json={"content": "plain comment without mention", "quote": "hello"},
            headers=headers,
        )
    assert r_c.status_code == 200, r_c.text
    assert _count(c_sql, _PROJECT_MEMBERS_SELECT) == 0, [
        s for s in c_sql if _PROJECT_MEMBERS_SELECT.search(s)
    ]
    assert _count(c_sql, _AGENT_INSTANCES_SELECT) == 0, [
        s for s in c_sql if _AGENT_INSTANCES_SELECT.search(s)
    ]
    assert len(c_sql) <= 9, (
        f"expected <= 9 SQL statements on POST /documents/{{id}}/comments, got {len(c_sql)}: {c_sql}"
    )


def test_create_topic_skips_redundant_refreshes_card_queries_and_post_flush_role_reads(
    client,
):
    """`POST /topics` reads `projects` once, avoids refreshing newly inserted
    `topics` and `topic_memberships` rows, and skips querying `accept_cards`
    and `notification` on a brand-new room."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as create_sql:
        r_create = client.post(
            "/topics",
            json={"project_id": pid, "title": "Fresh Channel"},
            headers=headers,
        )
    assert r_create.status_code == 200, r_create.text
    assert _count(create_sql, _PROJECTS_SELECT) == 1, [
        s for s in create_sql if _PROJECTS_SELECT.search(s)
    ]
    assert _count(create_sql, _TOPICS_SELECT) == 1, [
        s for s in create_sql if _TOPICS_SELECT.search(s)
    ]
    assert _count(create_sql, _ACCEPT_CARDS_SELECT) == 0, [
        s for s in create_sql if _ACCEPT_CARDS_SELECT.search(s)
    ]
    assert _count(create_sql, _NOTIFICATION_SELECT) == 0, [
        s for s in create_sql if _NOTIFICATION_SELECT.search(s)
    ]
    assert len(create_sql) <= 12, (
        f"expected <= 12 SQL statements on POST /topics, got {len(create_sql)}: {create_sql}"
    )


def test_add_and_remove_topic_member_deduplicate_membership_and_non_agent_dismiss_queries(
    client,
):
    """`POST /topics/{id}/members` and `DELETE /topics/{id}/members/{handle}`
    pre-load `topic` and `list_for_topic` so `resolve` and `_repo.get` reuse
    cached facts, and `DELETE` skips opening a `dismiss` session for a human handle."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]

    with counting_sql(client) as add_sql:
        r_add = client.post(
            f"/topics/{ch}/members",
            json={"handle": "bob"},
            headers=headers,
        )
    assert r_add.status_code == 200, r_add.text
    assert _count(add_sql, _CONVERSATIONS_KIND_SELECT) == 0
    assert _count(add_sql, _TOPICS_SELECT) == 1, [
        s for s in add_sql if _TOPICS_SELECT.search(s)
    ]
    assert len(add_sql) <= 17, (
        f"expected <= 17 SQL statements on POST /topics/{{id}}/members, got {len(add_sql)}: {add_sql}"
    )

    with counting_sql(client) as del_sql:
        r_del = client.delete(
            f"/topics/{ch}/members/bob",
            headers=headers,
        )
    assert r_del.status_code == 200, r_del.text
    assert _count(del_sql, _TOPICS_SELECT) == 1, [
        s for s in del_sql if _TOPICS_SELECT.search(s)
    ]
    assert _count(del_sql, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in del_sql if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert _count(del_sql, _AGENT_INSTANCES_SELECT) == 0, [
        s for s in del_sql if _AGENT_INSTANCES_SELECT.search(s)
    ]
    assert len(del_sql) <= 5, (
        f"expected <= 5 SQL statements on DELETE /topics/{{id}}/members/bob, got {len(del_sql)}: {del_sql}"
    )


_TASKS_UPDATE = re.compile(
    r"^\s*UPDATE\s+tasks\s+SET\b",
    re.IGNORECASE,
)


def test_create_task_consolidates_task_insert_and_skips_thread_and_lock_queries(client):
    """`POST /topics/{id}/tasks` writes `branch_name`, `workspace_name`,
    `base_branch`, `upgraded_from_block_id`, and `source_block_id` in the
    initial `INSERT INTO tasks` flush, skips probing `threads` on a fresh task
    UUID, and skips `FOR NO KEY UPDATE` + `SELECT projects.team_id` in
    `ensure_document`."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]

    with counting_sql(client) as task_sql:
        r_task = client.post(
            f"/topics/{ch}/tasks",
            json={"title": "Consolidated Task"},
            headers=headers,
        )
    assert r_task.status_code == 200, r_task.text
    assert _count(task_sql, _THREADS_IN_SELECT) == 0, [
        s for s in task_sql if _THREADS_IN_SELECT.search(s)
    ]
    assert _count(task_sql, _ALL_TASKS_SELECT) == 0, [
        s for s in task_sql if _ALL_TASKS_SELECT.search(s)
    ]
    assert _count(task_sql, _PROJECTS_SELECT) == 1, [
        s for s in task_sql if _PROJECTS_SELECT.search(s)
    ]
    assert _count(task_sql, _TASKS_UPDATE) == 1, [
        s for s in task_sql if _TASKS_UPDATE.search(s)
    ]
    assert len(task_sql) <= 14, (
        f"expected <= 14 SQL statements on POST /topics/{{id}}/tasks, got {len(task_sql)}: {task_sql}"
    )


def test_channel_join_leave_and_mark_read_skip_redundant_room_of_union_queries(client):
    """`POST /topics/{id}/join`, `POST /topics/{id}/leave`, and
    `POST /topics/{id}/read` reuse the resolved channel `Topic` row instead of
    running `room_of` (`SELECT inside.room_id FROM tasks UNION ALL threads`)."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    bob_headers = session_auth_headers("bob")
    ch = room_ids[0]

    with counting_sql(client) as join_sql:
        r_join = client.post(f"/topics/{ch}/join", json={}, headers=bob_headers)
    assert r_join.status_code == 200, r_join.text
    assert _count(join_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in join_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]

    with counting_sql(client) as leave_sql:
        r_leave = client.post(f"/topics/{ch}/leave", json={}, headers=bob_headers)
    assert r_leave.status_code == 200, r_leave.text
    assert _count(leave_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in leave_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert len(leave_sql) <= 6, (
        f"expected <= 6 SQL statements on POST /topics/{{id}}/leave, got {len(leave_sql)}: {leave_sql}"
    )

    with counting_sql(client) as read_sql:
        r_read = client.post(f"/topics/{ch}/read", json={}, headers=headers)
    assert r_read.status_code == 200, r_read.text
    assert _count(read_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in read_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert len(read_sql) <= 5, (
        f"expected <= 5 SQL statements on POST /topics/{{id}}/read, got {len(read_sql)}: {read_sql}"
    )


def test_upgrade_block_to_task_deduplicates_room_seat_and_task_queries(client):
    """`POST /blocks/{id}/upgrade` reuses the channel `Topic` row in
    `_main_line_room`, resolves `project_of_seat` without a full-table scan of
    `agent_instances`, checks `thread_teammate` in memory when `reply_count == 0`,
    and writes task fields in the initial `INSERT INTO tasks` flush."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    msg = post_message(client, ch, "alice", {"content": "upgrade me"})
    msg_id = msg["id"]

    with counting_sql(client) as up_sql:
        r_up = client.post(
            f"/blocks/{msg_id}/upgrade",
            json={"title": "Upgraded Task"},
            headers=headers,
        )
    assert r_up.status_code == 200, r_up.text
    assert _count(up_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in up_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert _count(up_sql, _CONVERSATIONS_KIND_SELECT) == 0, [
        s for s in up_sql if _CONVERSATIONS_KIND_SELECT.search(s)
    ]
    assert _count(up_sql, _THREADS_IN_SELECT) == 0, [
        s for s in up_sql if _THREADS_IN_SELECT.search(s)
    ]
    assert _count(up_sql, _TASKS_UPDATE) == 1, [
        s for s in up_sql if _TASKS_UPDATE.search(s)
    ]
    assert len(up_sql) <= 30, (
        f"expected <= 30 SQL statements on POST /blocks/{{id}}/upgrade, got {len(up_sql)}: {up_sql}"
    )


_DOC_THREADS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM document_threads\b",
    re.IGNORECASE,
)
_DOC_LOCKS_FOR_UPDATE = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM document_locks\b",
    re.IGNORECASE,
)
_BLOCKS_BY_ID_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM blocks\s+WHERE blocks\.id =",
    re.IGNORECASE,
)
_THREADS_BY_ROOT_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM threads\s+WHERE threads\.root_block_id =",
    re.IGNORECASE,
)


def test_document_comment_threads_and_mutations_batch_and_deduplicate_sql(client):
    """`GET /documents/{id}/comments/threads` batch-loads `document_threads`,
    replies, and `deliveries` in one query each instead of 3N queries, and
    `POST /documents/{id}/comments/{cid}/replies|resolve|reopen` checks
    `require_writable` before `journal.claim` flushes, locks the document once,
    and loads `DocumentThread` once."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    doc = client.post(
        f"/projects/{pid}/documents",
        json={"title": "Doc Threads", "content": "hello world body"},
        headers=headers,
    ).json()["data"]
    doc_id = doc["id"]
    cids = [
        client.post(
            f"/documents/{doc_id}/comments",
            json={"content": f"comment {i}", "quote": "hello"},
            headers=headers,
        ).json()["data"]["id"]
        for i in range(4)
    ]

    with counting_sql(client) as list_sql:
        r_list = client.get(
            f"/documents/{doc_id}/comments/threads",
            headers=headers,
        )
    assert r_list.status_code == 200, r_list.text
    assert _count(list_sql, _DOC_THREADS_SELECT) == 1, [
        s for s in list_sql if _DOC_THREADS_SELECT.search(s)
    ]
    assert len(list_sql) <= 10, (
        f"expected <= 10 SQL statements on GET /documents/{{id}}/comments/threads, got {len(list_sql)}: {list_sql}"
    )

    with counting_sql(client) as reply_sql:
        r_reply = client.post(
            f"/documents/{doc_id}/comments/{cids[0]}/replies",
            json={
                "content": "reply 1",
                "expected_revision": 1,
                "operation_id": str(uuid.uuid4()),
            },
            headers=headers,
        )
    assert r_reply.status_code == 200, r_reply.text
    assert _count(reply_sql, _PROJECTS_SELECT) == 1, [
        s for s in reply_sql if _PROJECTS_SELECT.search(s)
    ]
    assert _count(reply_sql, _DOC_LOCKS_FOR_UPDATE) == 1, [
        s for s in reply_sql if _DOC_LOCKS_FOR_UPDATE.search(s)
    ]
    assert _count(reply_sql, _DOC_THREADS_SELECT) == 1, [
        s for s in reply_sql if _DOC_THREADS_SELECT.search(s)
    ]
    assert len(reply_sql) <= 15, (
        f"expected <= 15 SQL statements on POST .../replies, got {len(reply_sql)}: {reply_sql}"
    )

    with counting_sql(client) as res_sql:
        r_res = client.post(
            f"/documents/{doc_id}/comments/{cids[1]}/resolve",
            json={"expected_revision": 1, "operation_id": str(uuid.uuid4())},
            headers=headers,
        )
    assert r_res.status_code == 200, r_res.text
    assert _count(res_sql, _PROJECTS_SELECT) == 1
    assert _count(res_sql, _DOC_LOCKS_FOR_UPDATE) == 1
    assert _count(res_sql, _DOC_THREADS_SELECT) == 1
    assert len(res_sql) <= 14, (
        f"expected <= 14 SQL statements on POST .../resolve, got {len(res_sql)}: {res_sql}"
    )


def test_open_and_create_channel_threads_reuse_block_and_returning_thread(client):
    """`POST /blocks/{id}/thread` and `POST /topics/{id}/threads` keep the root
    `Block` in `request_facts` across helper calls and use `RETURNING` on
    `INSERT INTO threads`, avoiding a second `SELECT blocks` and `SELECT threads`."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    msg = post_message(client, ch, "alice", {"content": "thread root"})
    msg_id = msg["id"]

    with counting_sql(client) as open_sql:
        r_open = client.post(f"/blocks/{msg_id}/thread", json={}, headers=headers)
    assert r_open.status_code == 200, r_open.text
    assert _count(open_sql, _BLOCKS_BY_ID_SELECT) == 1, [
        s for s in open_sql if _BLOCKS_BY_ID_SELECT.search(s)
    ]
    assert _count(open_sql, _THREADS_BY_ROOT_SELECT) == 0, [
        s for s in open_sql if _THREADS_BY_ROOT_SELECT.search(s)
    ]
    assert len(open_sql) <= 6, (
        f"expected <= 6 SQL statements on POST /blocks/{{id}}/thread, got {len(open_sql)}: {open_sql}"
    )

    with counting_sql(client) as create_sql:
        r_create = client.post(
            f"/topics/{ch}/threads",
            json={"title": "Named Thread"},
            headers=headers,
        )
    assert r_create.status_code == 200, r_create.text
    assert _count(create_sql, _BLOCKS_BY_ID_SELECT) == 0, [
        s for s in create_sql if _BLOCKS_BY_ID_SELECT.search(s)
    ]
    assert _count(create_sql, _THREADS_BY_ROOT_SELECT) == 0, [
        s for s in create_sql if _THREADS_BY_ROOT_SELECT.search(s)
    ]
    assert len(create_sql) <= 7, (
        f"expected <= 7 SQL statements on POST /topics/{{id}}/threads, got {len(create_sql)}: {create_sql}"
    )


def test_rename_topic_passes_room_to_authorize_topic(client):
    """`POST /topics/{id}/title` passes `room=place.room` to `authorize_topic`
    instead of running `room_of` (`SELECT inside.room_id FROM tasks UNION ALL threads`)."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]

    with counting_sql(client) as title_sql:
        r_title = client.post(
            f"/topics/{ch}/title",
            json={"title": "Renamed Channel"},
            headers=headers,
        )
    assert r_title.status_code == 200, r_title.text
    assert _count(title_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in title_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert len(title_sql) <= 6, (
        f"expected <= 6 SQL statements on POST /topics/{{id}}/title, got {len(title_sql)}: {title_sql}"
    )


_PROJECT_ROLES_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM project_roles\b",
    re.IGNORECASE,
)
_BLOCK_REACTIONS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM block_reactions\b",
    re.IGNORECASE,
)


def test_set_member_role_reuses_cached_standing_dependencies_before_commit(client):
    """`PUT /projects/{id}/members/{handle}/role` resolves `standing` before
    `commit()` and only invalidates `project_roles` on Core DML so `projects`,
    `user`, `team_user_relation`, `project_member_exclusions`, `project_role_defs`,
    and `project_members` are not re-queried."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as role_sql:
        r_role = client.put(
            f"/projects/{pid}/members/bob/role",
            json={"role": "admin"},
            headers=headers,
        )
    assert r_role.status_code == 200, r_role.text
    assert _count(role_sql, _PROJECTS_SELECT) == 1, [
        s for s in role_sql if _PROJECTS_SELECT.search(s)
    ]
    assert _count(role_sql, _PROJECT_MEMBERS_SELECT) == 1, [
        s for s in role_sql if _PROJECT_MEMBERS_SELECT.search(s)
    ]
    assert _count(role_sql, _PROJECT_ROLES_SELECT) == 1, [
        s for s in role_sql if _PROJECT_ROLES_SELECT.search(s)
    ]
    assert len(role_sql) <= 12, (
        f"expected <= 12 SQL statements on PUT /projects/{{id}}/members/bob/role, got {len(role_sql)}: {role_sql}"
    )


def test_get_thread_skips_topic_probe_and_redundant_thread_lookup_for_task(client):
    """`GET /topics/{id}/thread` resolves tasks and threads via `inner_place`
    without probing `topics` for `topic_id` first, and skips querying `threads`
    when `place.task.upgraded_from_block_id is None`."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    msg = post_message(client, ch, "alice", {"content": "thread root"})
    th = client.post(f"/blocks/{msg['id']}/thread", json={}, headers=headers).json()[
        "data"
    ]
    th_id = th["id"]
    tasks = client.get(f"/topics/{ch}/tasks?status=open", headers=headers).json()[
        "data"
    ]["data"]
    task_id = tasks[0]["id"]

    with counting_sql(client) as th_sql:
        r_th = client.get(f"/topics/{th_id}/thread", headers=headers)
    assert r_th.status_code == 200, r_th.text
    assert _count(th_sql, _TOPICS_SELECT) == 1, [
        s for s in th_sql if _TOPICS_SELECT.search(s)
    ]
    assert len(th_sql) <= 7, (
        f"expected <= 7 SQL statements on GET /topics/{{thread_id}}/thread, got {len(th_sql)}: {th_sql}"
    )

    with counting_sql(client) as task_sql:
        r_task = client.get(f"/topics/{task_id}/thread", headers=headers)
    assert r_task.status_code == 200, r_task.text
    assert _count(task_sql, _TOPICS_SELECT) == 1, [
        s for s in task_sql if _TOPICS_SELECT.search(s)
    ]
    assert len(task_sql) <= 5, (
        f"expected <= 5 SQL statements on GET /topics/{{task_id}}/thread, got {len(task_sql)}: {task_sql}"
    )


def test_toggle_reaction_reuses_room_and_reaction_rows(client):
    """`POST /blocks/{id}/reactions` resolves the room via `place_or_404`
    without `room_of` UNION ALL on channel messages and reads `block_reactions`
    once per toggle."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]
    msg = post_message(client, ch, "alice", {"content": "react to me"})
    msg_id = msg["id"]

    with counting_sql(client) as react_sql:
        r_react = client.post(
            f"/blocks/{msg_id}/reactions",
            json={"emoji": "👍"},
            headers=headers,
        )
    assert r_react.status_code == 200, r_react.text
    assert _count(react_sql, _ROOM_OF_UNION_SELECT) == 0, [
        s for s in react_sql if _ROOM_OF_UNION_SELECT.search(s)
    ]
    assert _count(react_sql, _BLOCK_REACTIONS_SELECT) == 1, [
        s for s in react_sql if _BLOCK_REACTIONS_SELECT.search(s)
    ]
    assert len(react_sql) <= 7, (
        f"expected <= 7 SQL statements on POST /blocks/{{id}}/reactions, got {len(react_sql)}: {react_sql}"
    )


_PROJECT_ROLE_DEFS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM project_role_defs\b",
    re.IGNORECASE,
)
_AGENT_BINDINGS_SELECT = re.compile(
    r"^\s*SELECT\b[^;]*?\bFROM agent_bindings\b",
    re.IGNORECASE,
)


def test_role_create_and_update_reuse_cached_role_defs_and_holders(client):
    """`POST /projects/{id}/roles` and `PATCH /projects/{id}/roles/{key}`
    resolve `get_def` and `named` from cached `project_role_defs`, retain the
    role definitions across `flush()`, and compute `holders` before `commit()`."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as create_sql:
        r_create = client.post(
            f"/projects/{pid}/roles",
            json={"name": "CustomRole", "copy_from": "member"},
            headers=headers,
        )
    assert r_create.status_code == 200, r_create.text
    rkey = r_create.json()["data"]["key"]
    assert _count(create_sql, _PROJECT_ROLE_DEFS_SELECT) == 1, [
        s for s in create_sql if _PROJECT_ROLE_DEFS_SELECT.search(s)
    ]
    assert len(create_sql) <= 6, (
        f"expected <= 6 SQL statements on POST /projects/{{id}}/roles, got {len(create_sql)}: {create_sql}"
    )

    with counting_sql(client) as update_sql:
        r_update = client.patch(
            f"/projects/{pid}/roles/{rkey}",
            json={"name": "RenamedRole"},
            headers=headers,
        )
    assert r_update.status_code == 200, r_update.text
    assert _count(update_sql, _PROJECTS_SELECT) == 1, [
        s for s in update_sql if _PROJECTS_SELECT.search(s)
    ]
    assert _count(update_sql, _PROJECT_ROLE_DEFS_SELECT) == 1, [
        s for s in update_sql if _PROJECT_ROLE_DEFS_SELECT.search(s)
    ]
    assert len(update_sql) <= 13, (
        f"expected <= 13 SQL statements on PATCH /projects/{{id}}/roles/{{key}}, got {len(update_sql)}: {update_sql}"
    )


def test_create_project_agent_skips_new_user_binding_and_profile_probes(client):
    """`POST /projects/{id}/agents` skips querying `agent_bindings` and
    `user_profile` for a freshly inserted agent `User` row and avoids refreshing
    `AgentBinding` after `flush()`."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")

    with counting_sql(client) as agent_sql:
        r_agent = client.post(
            f"/projects/{pid}/agents",
            json={"handle": "fast-helper", "display_name": "Fast Helper"},
            headers=headers,
        )
    assert r_agent.status_code == 200, r_agent.text
    assert _count(agent_sql, _AGENT_BINDINGS_SELECT) == 1, [
        s for s in agent_sql if _AGENT_BINDINGS_SELECT.search(s)
    ]
    assert len(agent_sql) <= 14, (
        f"expected <= 14 SQL statements on POST /projects/{{id}}/agents, got {len(agent_sql)}: {agent_sql}"
    )


def test_set_members_only_caches_single_topic_membership_lookup(client):
    """`PUT /topics/{id}/members-only` caches `("topic_member_row", topic_id, handle)`
    across `require_manager` and `_ensure_member` instead of querying
    `topic_memberships` for the same `(topic_id, member_handle)` twice."""
    _pid, room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch = room_ids[0]

    with counting_sql(client) as priv_sql:
        r_priv = client.put(
            f"/topics/{ch}/members-only",
            json={"members_only": True},
            headers=headers,
        )
    assert r_priv.status_code == 200, r_priv.text
    assert len(priv_sql) <= 6, (
        f"expected <= 6 SQL statements on PUT /topics/{{id}}/members-only, got {len(priv_sql)}: {priv_sql}"
    )


def test_agent_editing_lease_reuses_cached_standing_without_roster_build(client):
    """`PUT /projects/{id}/agents/{aid}/editing` checks `MemberService.manages`
    (which hits cached `standing` from `authorize_project`) before building the
    full project roster."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ag = client.post(
        f"/projects/{pid}/agents",
        json={"handle": "edit-bot", "display_name": "Edit Bot"},
        headers=headers,
    ).json()["data"]
    aid = ag.get("id") or ag.get("instance_id")

    with counting_sql(client) as edit_sql:
        r_edit = client.put(
            f"/projects/{pid}/agents/{aid}/editing",
            json={"held": True},
            headers=headers,
        )
    assert r_edit.status_code == 200, r_edit.text
    assert _count(edit_sql, _PROJECT_MEMBERS_SELECT) == 0, [
        s for s in edit_sql if _PROJECT_MEMBERS_SELECT.search(s)
    ]
    assert len(edit_sql) <= 5, (
        f"expected <= 5 SQL statements on PUT /projects/{{id}}/agents/{{aid}}/editing, got {len(edit_sql)}: {edit_sql}"
    )


def test_archive_and_unarchive_channel_skip_redundant_children_tasks_and_project_reads(
    client,
):
    """`POST /topics/{id}/archive` and `POST /topics/{id}/unarchive` pre-load
    `topic_memberships` once in `administers`, resolve `_where_said` before
    dirtying the session, and skip `task_conversations_in` / `close_tasks` /
    `_archive_children` when archiving a childless channel with 0 open tasks."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch2 = client.post(
        "/topics",
        json={"project_id": pid, "title": "Archivable Ch"},
        headers=headers,
    ).json()["data"]["id"]

    with counting_sql(client) as arch_sql:
        r_arch = client.post(f"/topics/{ch2}/archive", json={}, headers=headers)
    assert r_arch.status_code == 200, r_arch.text
    assert _count(arch_sql, _PROJECTS_SELECT) == 1, [
        s for s in arch_sql if _PROJECTS_SELECT.search(s)
    ]
    assert len(arch_sql) <= 15, (
        f"expected <= 15 SQL statements on POST /topics/{{id}}/archive, got {len(arch_sql)}: {arch_sql}"
    )

    with counting_sql(client) as unarch_sql:
        r_unarch = client.post(f"/topics/{ch2}/unarchive", json={}, headers=headers)
    assert r_unarch.status_code == 200, r_unarch.text
    assert _count(unarch_sql, _PROJECTS_SELECT) == 1, [
        s for s in unarch_sql if _PROJECTS_SELECT.search(s)
    ]
    assert len(unarch_sql) <= 12, (
        f"expected <= 12 SQL statements on POST /topics/{{id}}/unarchive, got {len(unarch_sql)}: {unarch_sql}"
    )


def test_hand_over_channel_reuses_roster_is_agent_and_list_for_topic_rows(client):
    """`PUT /projects/{id}/channels/{cid}/manager` reuses `("is_agent", handle)`
    populated by `_on_project` (`roster`) and finds `seat` inside the
    `list_for_topic` loop without triggering a premature autoflush and second
    `topic_memberships` query."""
    pid, _room_ids = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    ch2 = client.post(
        "/topics",
        json={"project_id": pid, "title": "Handover Ch"},
        headers=headers,
    ).json()["data"]["id"]

    with counting_sql(client) as ho_sql:
        r_ho = client.put(
            f"/projects/{pid}/channels/{ch2}/manager",
            json={"handle": "bob"},
            headers=headers,
        )
    assert r_ho.status_code == 200, r_ho.text
    assert _count(ho_sql, _TOPIC_MEMBERSHIPS_SELECT) == 1, [
        s for s in ho_sql if _TOPIC_MEMBERSHIPS_SELECT.search(s)
    ]
    assert len(ho_sql) <= 18, (
        f"expected <= 18 SQL statements on PUT /projects/{{id}}/channels/{{cid}}/manager, got {len(ho_sql)}: {ho_sql}"
    )
