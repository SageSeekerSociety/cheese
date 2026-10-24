"""`GET /run-records/turns`: one row per turn in the window, and what became of
it.

Run records (`/run-records`) answer "how often did this kind of thing happen" —
one line per kind, which is no denominator at all. This reads the denominator:
every turn `agent_turns` holds in the window, with the terminal failure event it
left behind (none ⇒ nothing was recorded, NOT that it succeeded) and how often
its prompt batch had been delivered by the time this turn carried it.

Only a room inside the platform's own project may read it: the rows name other
projects' conversations.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.agent.models import (
    STOP_FINISHED,
    STOP_STOPPED,
    STOP_SWEEP,
    AgentTurn,
)
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.run_record.models import RunRecord
from tests.integration.conftest import (
    post_project,
    room_agent_headers,
    session_auth_headers,
)

DEV = "turns-dev"


def _room(client, project: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "分母"},
        headers=session_auth_headers(DEV),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


@pytest.fixture
def platform_room(client, monkeypatch) -> tuple[str, str]:
    """The platform's own project and a room in it, which is where this reads."""
    project = post_project(
        client, json={"name": "知是"}, headers=session_auth_headers(DEV)
    ).json()["data"]["id"]
    monkeypatch.setattr(
        settings, "docs_dev_repositories", [f"project-{uuid.UUID(project).hex}/code"]
    )
    return project, _room(client, project)


def _seed(client, project: str, room: str, specs: list[dict]) -> list[uuid.UUID]:
    """Turns, the terminal events they left, and the prompts that carried them.

    A spec is `started_at`, optional `stopped_at`, `events` (the terminal events
    the platform posted, in that order — a code, or `meta` to write verbatim),
    `prompted` (extra `meta` for the message blocks this turn's prompt batch was
    built from — the claim is filled in, since a block fed to a turn is claimed
    by it) and `records` (the run records this turn left — `kind`, optional
    `severity` and `content`).
    """
    turn_ids: list[uuid.UUID] = []

    async def write() -> None:
        async with client.test_factory() as session:
            for spec in specs:
                turn_id = uuid.uuid4()
                turn_ids.append(turn_id)
                started = spec["started_at"]
                session.add(
                    AgentTurn(
                        id=turn_id,
                        conversation_id=uuid.UUID(room),
                        continuation_id=turn_id,
                        author="alice",
                        content="做点事",
                        started_at=started,
                        stopped_at=spec.get("stopped_at"),
                        stop_reason=spec.get("stop_reason"),
                    )
                )
                for i, event in enumerate(spec.get("events", [])):
                    meta = (
                        {"event_type": event} if isinstance(event, str) else dict(event)
                    )
                    session.add(
                        Block(
                            project_id=uuid.UUID(project),
                            conversation_id=uuid.UUID(room),
                            author="platform",
                            author_type=AuthorType.platform,
                            kind=BlockKind.event,
                            content="",
                            turn_id=turn_id,
                            meta=meta,
                            created_at=started + timedelta(seconds=i + 1),
                        )
                    )
                for i, extra in enumerate(spec.get("prompted", [])):
                    session.add(
                        Block(
                            project_id=uuid.UUID(project),
                            conversation_id=uuid.UUID(room),
                            author="alice",
                            author_type=AuthorType.participant,
                            kind=BlockKind.message,
                            content=f"做点事 {i}",
                            meta={"prompted_turn": str(turn_id), **extra},
                            created_at=started - timedelta(seconds=i + 1),
                        )
                    )
                for i, record in enumerate(spec.get("records", [])):
                    session.add(
                        RunRecord(
                            project_id=uuid.UUID(project),
                            conversation_id=uuid.UUID(room),
                            turn_id=turn_id,
                            kind=record["kind"],
                            severity=record.get("severity", "info"),
                            content=record.get("content", ""),
                            created_at=started + timedelta(seconds=i + 1),
                        )
                    )
            await session.commit()

    client.portal.call(write)
    return turn_ids


def _turns(client, topic: str, **params) -> dict:
    response = client.get(
        "/run-records/turns",
        params={"topic": topic, **params},
        headers=room_agent_headers(client, topic),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_one_row_per_turn_with_its_terminal_event(client, platform_room):
    project, room = platform_room
    now = datetime.now(UTC)
    clean, failed, open_turn, timed_out = _seed(
        client,
        project,
        room,
        [
            {
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(minutes=55),
                "prompted": [{"prompt_attempts": 2}],
            },
            {
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=2) + timedelta(minutes=4),
                "events": ["turn_failed"],
            },
            # Still running: no `stopped_at`, which is what "running" means.
            {"started_at": now - timedelta(hours=3)},
            # A timeout counts here as not having finished, though the room shows
            # no red card for one (`block.indexed_rows.FAILED_TURN_EVENTS`).
            {
                "started_at": now - timedelta(hours=4),
                "stopped_at": now - timedelta(hours=4) + timedelta(minutes=1),
                "events": ["turn_timeout"],
            },
        ],
    )

    data = _turns(client, room, window="24h")

    assert data["total"] == 4
    assert data["window"] == "24h"
    assert data["limit"] == 200
    assert [t["turn_id"] for t in data["turns"]] == [
        str(clean),
        str(failed),
        str(open_turn),
        str(timed_out),
    ]
    by_id = {t["turn_id"]: t for t in data["turns"]}
    # No event was written ⇒ nothing is claimed about how it ended.
    assert by_id[str(clean)]["failure_event"] is None
    assert by_id[str(clean)]["stopped_at"] is not None
    assert by_id[str(failed)]["failure_event"] == "turn_failed"
    assert by_id[str(timed_out)]["failure_event"] == "turn_timeout"
    # `stopped_at` empty is "still open", not "unknown".
    assert by_id[str(open_turn)]["stopped_at"] is None
    assert by_id[str(open_turn)]["failure_event"] is None
    assert all(t["conversation_id"] == room for t in data["turns"])


def test_a_notice_that_borrows_the_failure_code_is_not_a_failure(client, platform_room):
    """The platform reuses `turn_failed` for a few "nothing is wrong, it is just
    waiting" notices, told apart only by `meta.severity`. Counting those would
    report a healthy turn as broken and inflate the very denominator this reads.
    """
    project, room = platform_room
    now = datetime.now(UTC)
    notice, broke, quiet = _seed(
        client,
        project,
        room,
        [
            # `own_calls.say_refused` / the waiting notice: a real turn, an info
            # notice, no failure.
            {
                "started_at": now - timedelta(minutes=6),
                "stopped_at": now - timedelta(minutes=5),
                "events": [{"event_type": "turn_failed", "severity": "info"}],
            },
            {
                "started_at": now - timedelta(minutes=16),
                "stopped_at": now - timedelta(minutes=15),
                "events": [{"event_type": "turn_failed", "severity": "error"}],
            },
            # A warning is not a notice: timeouts and deploy interruptions count.
            {
                "started_at": now - timedelta(minutes=26),
                "stopped_at": now - timedelta(minutes=25),
                "events": [{"event_type": "turn_timeout", "severity": "warn"}],
            },
        ],
    )

    by_id = {t["turn_id"]: t for t in _turns(client, room, window="24h")["turns"]}
    assert by_id[str(notice)]["failure_event"] is None
    assert by_id[str(broke)]["failure_event"] == "turn_failed"
    assert by_id[str(quiet)]["failure_event"] == "turn_timeout"


def test_the_last_terminal_event_is_the_one_reported(client, platform_room):
    """One turn can leave two terminal events — a timeout, then the fault the
    platform recognised afterwards. The later one is what it ended as."""
    project, room = platform_room
    now = datetime.now(UTC)
    (turn_id,) = _seed(
        client,
        project,
        room,
        [
            {
                "started_at": now - timedelta(minutes=8),
                "stopped_at": now - timedelta(minutes=7),
                "events": [
                    {"event_type": "turn_timeout", "severity": "warn"},
                    {"event_type": "platform_error", "severity": "error"},
                ],
            }
        ],
    )

    by_id = {t["turn_id"]: t for t in _turns(client, room, window="24h")["turns"]}
    assert by_id[str(turn_id)]["failure_event"] == "platform_error"


def test_a_block_that_claims_no_turn_is_not_read_into_the_count(client, platform_room):
    """The prompt counts are grouped by the turn a block claims, so blocks that
    claim none must never reach that group-by: the claim is the only thing
    naming the turn, and without it the read is grouping by nothing."""
    project, room = platform_room
    now = datetime.now(UTC)
    (carried,) = _seed(
        client,
        project,
        room,
        [
            {
                "started_at": now - timedelta(minutes=4),
                "stopped_at": now - timedelta(minutes=3),
                "prompted": [{"prompt_attempts": 2}],
            }
        ],
    )

    async def unclaimed() -> None:
        async with client.test_factory() as session:
            session.add(
                Block(
                    project_id=uuid.UUID(project),
                    conversation_id=uuid.UUID(room),
                    author="alice",
                    author_type=AuthorType.participant,
                    kind=BlockKind.message,
                    content="没人认领",
                    meta={"prompt_attempts": 9},
                    created_at=now - timedelta(minutes=4),
                )
            )
            await session.commit()

    client.portal.call(unclaimed)

    data = _turns(client, room, window="24h")
    by_id = {t["turn_id"]: t for t in data["turns"]}
    assert by_id[str(carried)]["prompt_attempts"] == 2
    assert data["total"] == 1


def test_the_highest_count_of_the_batch_is_the_one_reported(client, platform_room):
    project, room = platform_room
    now = datetime.now(UTC)
    carried, carried_nothing = _seed(
        client,
        project,
        room,
        [
            {
                "started_at": now - timedelta(minutes=10),
                "stopped_at": now - timedelta(minutes=5),
                "prompted": [{"prompt_attempts": 1}, {"prompt_attempts": 3}],
            },
            {
                "started_at": now - timedelta(minutes=20),
                "stopped_at": now - timedelta(minutes=15),
            },
        ],
    )

    async def elsewhere() -> None:
        # A message block claimed by a turn that is not in this page — a turn of
        # another room, or one that has not started yet. Nobody here reads it.
        async with client.test_factory() as session:
            session.add(
                Block(
                    project_id=uuid.UUID(project),
                    conversation_id=uuid.UUID(room),
                    author="alice",
                    author_type=AuthorType.participant,
                    kind=BlockKind.message,
                    content="别人的",
                    meta={"prompted_turn": str(uuid.uuid4()), "prompt_attempts": 9},
                    created_at=now - timedelta(minutes=20),
                )
            )
            await session.commit()

    client.portal.call(elsewhere)

    data = _turns(client, room, window="24h")
    by_id = {t["turn_id"]: t for t in data["turns"]}
    assert by_id[str(carried)]["prompt_attempts"] == 3
    # No message block claims this turn: a platform-authored turn, or one whose
    # claim was withdrawn when it failed.
    assert by_id[str(carried_nothing)]["prompt_attempts"] is None


def test_a_window_with_no_turn_is_an_empty_page_not_a_crash(client, platform_room):
    """A room that has run nothing yet gets an empty page, not a 500.

    The last-frame pass reads the envelope's `turns`; an empty window has
    `turns == []`, and reading it by truthiness (`[] or [envelope]`) would
    hand the envelope itself to the per-turn pass — a dict with no `turn_id`
    — and blow up on the very case where there is nothing to report.
    """
    _, room = platform_room

    data = _turns(client, room, window="24h")

    assert data["turns"] == []
    assert data["total"] == 0


def test_the_window_holds_rows_back_and_the_limit_only_pages(client, platform_room):
    project, room = platform_room
    now = datetime.now(UTC)
    recent, older, oldest = _seed(
        client,
        project,
        room,
        [
            {"started_at": now - timedelta(hours=2)},
            {"started_at": now - timedelta(hours=30)},
            {"started_at": now - timedelta(days=20)},
        ],
    )

    day = _turns(client, room, window="24h")
    assert [t["turn_id"] for t in day["turns"]] == [str(recent)]
    assert day["total"] == 1

    week = _turns(client, room, window="7d")
    assert [t["turn_id"] for t in week["turns"]] == [str(recent), str(older)]
    assert week["total"] == 2

    month = _turns(client, room, window="30d", limit=2)
    assert [t["turn_id"] for t in month["turns"]] == [str(recent), str(older)]
    # The rows past the limit are counted, not lost.
    assert month["total"] == 3
    assert month["limit"] == 2
    assert datetime.fromisoformat(month["now"]) - datetime.fromisoformat(
        month["since"]
    ) == timedelta(days=30)


def test_a_room_in_another_project_is_refused(client, platform_room):
    other = post_project(
        client, json={"name": "别的"}, headers=session_auth_headers(DEV)
    ).json()["data"]["id"]
    elsewhere = _room(client, other)

    response = client.get(
        "/run-records/turns",
        params={"topic": elsewhere},
        headers=room_agent_headers(client, elsewhere),
    )
    assert response.status_code == 403, response.text
    assert "知是代码仓库" in response.text


def _one_turn(client, topic: str, turn_id):
    return client.get(
        f"/run-records/turns/{turn_id}",
        params={"topic": topic},
        headers=room_agent_headers(client, topic),
    )


def test_one_turn_answers_with_its_records_and_how_it_ended(client, platform_room):
    project, room = platform_room
    now = datetime.now(UTC)
    turn_id, other_turn = _seed(
        client,
        project,
        room,
        [
            {
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=1),
                "events": [{"event_type": "turn_failed", "severity": "error"}],
                "prompted": [{"prompt_attempts": 2}],
                "records": [
                    {"kind": "queue", "content": "排队"},
                    {
                        "kind": "turn_failed",
                        "severity": "error",
                        "content": "这一轮没跑完",
                    },
                ],
            },
            # Another turn in the SAME room, with records of its own: the
            # drill-down must return this turn's rows only. Without the
            # `turn_id` filter in `RunRecordRepository.of_turns`, these leak
            # into the first turn's answer.
            {
                "started_at": now - timedelta(hours=4),
                "stopped_at": now - timedelta(hours=3),
                "records": [
                    {"kind": "environment", "content": "别人那一轮"},
                    {"kind": "retry", "content": "别人又一条"},
                ],
            },
        ],
    )

    response = _one_turn(client, room, turn_id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["turn_id"] == str(turn_id)
    assert data["conversation_id"] == room
    assert data["status"] == "failed"
    assert data["failure_event"] == "turn_failed"
    assert data["prompt_attempts"] == 2
    assert data["stopped_at"] is not None
    # Only this turn's records, oldest first, and only the fields a reader
    # needs to see what happened. `other_turn`'s two rows are in the same room
    # and the same table and must not appear here.
    assert [(r["kind"], r["severity"], r["content"]) for r in data["records"]] == [
        ("queue", "info", "排队"),
        ("turn_failed", "error", "这一轮没跑完"),
    ]
    assert all("别人" not in r["content"] for r in data["records"])
    # And the other turn is answered with its own rows, not this turn's.
    other = _one_turn(client, room, other_turn).json()["data"]
    assert [r["content"] for r in other["records"]] == ["别人那一轮", "别人又一条"]


def test_a_closed_turn_without_a_failure_event_is_unfinished(client, platform_room):
    """`stopped_at` says the row closed, not that the turn succeeded.

    Success writes no event, so the only honest answer for a closed turn with
    no terminal failure is `unfinished`: the read proves nothing was recorded
    against it, which is not the same thing as a clean finish.
    """
    project, room = platform_room
    now = datetime.now(UTC)
    (turn_id,) = _seed(
        client,
        project,
        room,
        [
            {
                "started_at": now - timedelta(hours=3),
                "stopped_at": now - timedelta(hours=2, minutes=55),
                "records": [{"kind": "environment", "content": "机器连上了"}],
            }
        ],
    )

    response = _one_turn(client, room, turn_id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["status"] == "unfinished"
    assert data["status"] != "done"
    assert data["failure_event"] is None
    assert data["stopped_at"] is not None


def test_an_unknown_turn_id_is_not_found(client, platform_room):
    _, room = platform_room

    response = _one_turn(client, room, uuid.uuid4())

    assert response.status_code == 404, response.text


def test_a_turn_id_that_is_not_a_full_uuid_is_refused(client, platform_room):
    """A short prefix names nothing the turn table can answer, and the shared
    validation handler makes that a 400 rather than a lookup."""
    project, room = platform_room
    now = datetime.now(UTC)
    (turn_id,) = _seed(
        client, project, room, [{"started_at": now - timedelta(minutes=5)}]
    )

    response = _one_turn(client, room, str(turn_id)[:8])

    assert response.status_code == 400, response.text


def test_an_open_turn_with_no_readable_session_says_unknown_not_zero(
    client, platform_room
):
    """This test backend reads no session for the turn, so its last frame is
    unknown — stated as a null, which a reader must not take for "no frame for
    0 seconds". A closed turn is not asked at all: its frame is history."""
    project, room = platform_room
    now = datetime.now(UTC)
    open_turn, closed = _seed(
        client,
        project,
        room,
        [
            {"started_at": now - timedelta(minutes=5)},
            {
                "started_at": now - timedelta(minutes=15),
                "stopped_at": now - timedelta(minutes=10),
            },
        ],
    )

    by_id = {t["turn_id"]: t for t in _turns(client, room, window="24h")["turns"]}

    assert by_id[str(open_turn)]["last_frame_kind"] is None
    assert by_id[str(open_turn)]["last_frame_age_s"] is None
    assert "last_frame_kind" not in by_id[str(closed)]

    one = _one_turn(client, room, open_turn).json()["data"]
    assert one["last_frame_kind"] is None
    assert one["last_frame_age_s"] is None


def test_another_projects_room_cannot_read_one_turn(client, platform_room):
    project, room = platform_room
    now = datetime.now(UTC)
    (turn_id,) = _seed(
        client, project, room, [{"started_at": now - timedelta(minutes=5)}]
    )
    other = post_project(
        client, json={"name": "别的"}, headers=session_auth_headers(DEV)
    ).json()["data"]["id"]
    elsewhere = _room(client, other)

    response = client.get(
        f"/run-records/turns/{turn_id}",
        params={"topic": elsewhere},
        headers=room_agent_headers(client, elsewhere),
    )

    assert response.status_code == 403, response.text
    assert "知是代码仓库" in response.text


def test_a_turn_says_which_closer_ended_it(client, platform_room):
    """`stopped_at` says when the row closed. `stop_reason` says who closed it —
    the one question left after a turn sat open for thirteen hours: was it the
    session finishing, the sweep, or the task being closed underneath it."""
    project, room = platform_room
    now = datetime.now(UTC)
    finished, swept, open_turn = _seed(
        client,
        project,
        room,
        [
            {
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(minutes=55),
                "stop_reason": STOP_FINISHED,
            },
            {
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=1, minutes=30),
                "stop_reason": STOP_SWEEP,
            },
            {"started_at": now - timedelta(hours=3)},
        ],
    )

    by_id = {t["turn_id"]: t for t in _turns(client, room, window="24h")["turns"]}

    assert by_id[str(finished)]["stop_reason"] == STOP_FINISHED
    assert by_id[str(swept)]["stop_reason"] == STOP_SWEEP
    # Still running: nothing closed it, so nothing says who did.
    assert by_id[str(open_turn)]["stop_reason"] is None


def test_closing_a_turn_by_hand_records_the_closer(client, platform_room):
    """The read is only worth anything if the writers actually write it."""
    project, room = platform_room
    now = datetime.now(UTC)
    (turn_id,) = _seed(
        client, project, room, [{"started_at": now - timedelta(minutes=5)}]
    )

    async def close() -> None:
        async with client.test_factory() as session:
            await AgentTurnRepository(session).close(
                [turn_id], datetime.now(UTC), reason=STOP_STOPPED
            )
            await session.commit()

    client.portal.call(close)

    by_id = {t["turn_id"]: t for t in _turns(client, room, window="24h")["turns"]}
    assert by_id[str(turn_id)]["stop_reason"] == STOP_STOPPED
