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

from app.domain.agent.models import AgentTurn
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.services import threads_of_rooms, threads_still_threads
from app.domain.event_author import AuthorType
from app.domain.review.models import AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.services import TaskService
from app.domain.thread.services import open_thread
from app.domain.topic.services import TopicService
from tests.integration.conftest import (
    join_project_team,
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
    "status",
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
