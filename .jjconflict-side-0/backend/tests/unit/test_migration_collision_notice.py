"""两张未决卡各带一个新迁移 → 房间里说一声 (#314).

On 2026-08-11 a parent topic and the sub-topic it had split the work to each
filed a card. The parent's carried `op.add_column("topics", "progress")`; the
child's carried `op.create_table("topic_progress")`. Same feature, two
incompatible data models, two green cards, and nothing anywhere connecting
them — it was found by a human comparing file lists while chasing something
else. Both landing would have forked the alembic chain (#312) on top of
shipping the feature twice.

The judgement "these two are the same work" needs a person. What a machine can
contribute is noticing that two live cards each ADD an alembic revision, which
is a clean, purely mechanical predicate — and saying so while someone is
looking at the card.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.review.models import AcceptStatus
from app.domain.review.services import _CARD_BLOCKS_NEW_CARD, AcceptService


class _Recorder:
    """Captures what would be posted into the room, instead of posting it."""

    def __init__(self) -> None:
        self.messages: list[str] = []


def _service(
    monkeypatch,
    *,
    added_by_topic: dict[uuid.UUID, list[str]],
    live: list[SimpleNamespace],
    titles: dict[uuid.UUID, str],
    recorder: _Recorder,
    explode: bool = False,
) -> AcceptService:
    service = AcceptService.__new__(AcceptService)  # no session needed

    async def list_live_in_project(project_id, *, statuses):
        # Undecided cards only. A card already rejected/accepted cannot collide
        # with anything, and asking about them would make this fire forever.
        assert statuses == _CARD_BLOCKS_NEW_CARD
        return live

    async def get_topic(topic_id):
        title = titles.get(topic_id)
        return SimpleNamespace(id=topic_id, title=title) if title else None

    service._repo = SimpleNamespace(list_live_in_project=list_live_in_project)  # type: ignore[attr-defined]
    service._topics = SimpleNamespace(get=get_topic)  # type: ignore[attr-defined]
    service._notify_merge_result = lambda topic, content: recorder.messages.append(  # type: ignore[attr-defined]
        content
    )

    def added(project_id, topic_id):
        if explode:
            raise RuntimeError("git is unavailable in this sandbox")
        return added_by_topic.get(topic_id, [])

    # Via monkeypatch, not a bare assignment: this patches a module OTHER tests
    # in the same process import, and an unrestored stub there is the "a batch
    # of tests you never touched went red" failure in .claude/rules.
    monkeypatch.setattr(
        "app.domain.workspace.service.topic_added_files", added, raising=True
    )
    return service


PROJECT = uuid.uuid4()
MINE = uuid.uuid4()
SIBLING = uuid.uuid4()
MIGRATION = "backend/alembic/versions/c4a17b93d2e8_topic_progress.py"
OTHER_MIGRATION = "backend/alembic/versions/d41c9b7a2e18_topics_progress_column.py"


def _topic(topic_id: uuid.UUID = MINE) -> SimpleNamespace:
    return SimpleNamespace(id=topic_id, project_id=PROJECT, title="我这间房")


def _live_card(topic_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(topic_id=topic_id, status=AcceptStatus.pending)


@pytest.mark.anyio
async def test_two_live_cards_each_adding_a_migration_are_flagged(monkeypatch):
    rec = _Recorder()
    service = _service(
        monkeypatch,
        added_by_topic={MINE: [MIGRATION], SIBLING: [OTHER_MIGRATION]},
        live=[_live_card(MINE), _live_card(SIBLING)],
        titles={SIBLING: "进度层与记忆落地"},
        recorder=rec,
    )

    await service._warn_about_a_second_pending_migration(_topic())

    assert len(rec.messages) == 1
    said = rec.messages[0]
    assert "进度层与记忆落地" in said  # names the room to go look at
    assert "不拦" in said  # and says it is not blocking


@pytest.mark.anyio
async def test_one_migration_alone_is_silent(monkeypatch):
    """The common case by far. A notice that fires on every migration is a
    notice everyone learns to ignore."""
    rec = _Recorder()
    service = _service(
        monkeypatch,
        added_by_topic={MINE: [MIGRATION], SIBLING: ["backend/app/api/routes/x.py"]},
        live=[_live_card(MINE), _live_card(SIBLING)],
        titles={SIBLING: "别的活"},
        recorder=rec,
    )

    await service._warn_about_a_second_pending_migration(_topic())

    assert rec.messages == []


@pytest.mark.anyio
async def test_a_card_with_no_migration_is_silent_even_next_to_one(monkeypatch):
    rec = _Recorder()
    service = _service(
        monkeypatch,
        added_by_topic={MINE: ["frontend/src/x.ts"], SIBLING: [MIGRATION]},
        live=[_live_card(MINE), _live_card(SIBLING)],
        titles={SIBLING: "带迁移的那间"},
        recorder=rec,
    )

    await service._warn_about_a_second_pending_migration(_topic())

    assert rec.messages == []


@pytest.mark.anyio
async def test_a_topic_does_not_collide_with_its_own_card(monkeypatch):
    """The freshly-filed card is itself in the live list — without excluding it,
    every single migration card would warn about itself."""
    rec = _Recorder()
    service = _service(
        monkeypatch,
        added_by_topic={MINE: [MIGRATION]},
        live=[_live_card(MINE)],
        titles={},
        recorder=rec,
    )

    await service._warn_about_a_second_pending_migration(_topic())

    assert rec.messages == []


@pytest.mark.anyio
async def test_it_names_every_colliding_room_not_just_the_first(monkeypatch):
    third = uuid.uuid4()
    rec = _Recorder()
    service = _service(
        monkeypatch,
        added_by_topic={
            MINE: [MIGRATION],
            SIBLING: [OTHER_MIGRATION],
            third: ["backend/alembic/versions/aaa_third.py"],
        },
        live=[_live_card(MINE), _live_card(SIBLING), _live_card(third)],
        titles={SIBLING: "第二间", third: "第三间"},
        recorder=rec,
    )

    await service._warn_about_a_second_pending_migration(_topic())

    assert "第二间" in rec.messages[0]
    assert "第三间" in rec.messages[0]


@pytest.mark.anyio
async def test_a_broken_git_read_stays_silent_rather_than_failing_the_card(monkeypatch):
    """This is a diagnostic. Filing a card must not depend on it working."""
    rec = _Recorder()
    service = _service(
        monkeypatch,
        added_by_topic={MINE: [MIGRATION], SIBLING: [OTHER_MIGRATION]},
        live=[_live_card(MINE), _live_card(SIBLING)],
        titles={SIBLING: "第二间"},
        recorder=rec,
        explode=True,
    )

    await service._warn_about_a_second_pending_migration(_topic())

    assert rec.messages == []


@pytest.mark.anyio
async def test_a_sibling_whose_topic_row_vanished_still_gets_named(monkeypatch):
    """Never swallow the warning because the title lookup came back empty — the
    id is worse to read than a title, and far better than silence."""
    rec = _Recorder()
    service = _service(
        monkeypatch,
        added_by_topic={MINE: [MIGRATION], SIBLING: [OTHER_MIGRATION]},
        live=[_live_card(MINE), _live_card(SIBLING)],
        titles={},
        recorder=rec,
    )

    await service._warn_about_a_second_pending_migration(_topic())

    assert str(SIBLING) in rec.messages[0]
