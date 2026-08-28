"""watch_dogfood_push: after push_back() starts scripts/on-dogfood-push.sh
detached, this watches it in the background and posts what happened (deployed
/ rolled back / unknown) into the topic timeline. No real docker/redeploy
available here, so process exit + log content are stubbed; only "did the
right event land on the right topic" is verified."""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.agent.platform_notices import SEVERITY_ERROR, SEVERITY_INFO
from app.domain.block.models import AuthorType, BlockKind
from app.domain.workspace import dogfood_notices as dn


class FakeProc:
    """Stands in for the subprocess.Popen handle push_back() hands off."""

    def __init__(self, *, exit_after: float = 0.0, exit_code: int = 0):
        self._exit_after = exit_after
        self.exit_code = exit_code

    def wait(self) -> int:
        if self._exit_after:
            import time

            time.sleep(self._exit_after)
        return self.exit_code


class FakeTopic:
    def __init__(self, topic_id: uuid.UUID, project_id: uuid.UUID):
        self.id = topic_id
        self.project_id = project_id


class FakeTopicRepository:
    def __init__(self, session, topic: FakeTopic | None):
        self._topic = topic

    async def get(self, topic_id: uuid.UUID) -> FakeTopic | None:
        return self._topic


class FakeBlockRepository:
    """Production code makes a fresh BlockRepository(session) per retry
    attempt, so the remaining-failures counter must live OUTSIDE any single
    instance (a shared dict) or every attempt would see a freshly-reset
    countdown and never actually succeed."""

    def __init__(self, session, sink: list, remaining: dict):
        self._sink = sink
        self._remaining = remaining

    async def add(self, **kwargs) -> SimpleNamespace:
        if self._remaining["fail"] > 0:
            self._remaining["fail"] -= 1
            raise RuntimeError("db unavailable")
        self._sink.append(kwargs)
        # Production now validates the persisted row through BlockOut before it
        # publishes the realtime event_block, so `add` must hand back an object
        # BlockOut can read via from_attributes — returning None made that
        # validation raise, retry 3x, and land 3 rows in the sink.
        return SimpleNamespace(
            id=uuid.uuid4(),
            topic_id=kwargs["topic_id"],
            kind=kwargs["kind"],
            author_type=kwargs["author_type"],
            author=kwargs["author"],
            content=kwargs["content"],
            reply_to=None,
            struct_parent=None,
            node_type=None,
            struct_order=None,
            anchor_quote=None,
            mime_type=None,
            refs=[],
            upgraded_to_topic_id=None,
            turn_id=None,
            meta=None,
            reactions=[],
            created_at=datetime.now(UTC),
        )


class FakeSession:
    async def commit(self) -> None:
        pass


class FakeSessionCM:
    def __init__(self, session: FakeSession):
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _wire_fake_db(monkeypatch, *, topic: FakeTopic | None, fail_times: int = 0):
    """Point dogfood_notices at fakes instead of a real Postgres session, and
    return the list that captures every BlockRepository.add() call."""
    sink: list = []
    remaining = {"fail": fail_times}
    monkeypatch.setattr(
        dn, "async_session_factory", lambda: FakeSessionCM(FakeSession())
    )
    monkeypatch.setattr(
        dn, "TopicRepository", lambda session: FakeTopicRepository(session, topic)
    )
    monkeypatch.setattr(
        dn,
        "BlockRepository",
        lambda session: FakeBlockRepository(session, sink, remaining),
    )
    return sink


_DONE_LOG = "=== push-back done: platform now runs x ===\n"


def _write_log(tmp_path: Path, prefix: str, suffix: str) -> tuple[Path, int]:
    log = tmp_path / "tmp_dogfood_push.log"
    log.write_text(prefix)
    offset = log.stat().st_size
    with open(log, "a") as f:
        f.write(suffix)
    return log, offset


# ---- _compose_message: pure outcome parsing -------------------------------


BRANCH = "dogfood/abcd1234"


def test_compose_message_success_extracts_commit():
    text = "[..] [dogfood/abcd1234] === push-back done: platform now runs a1b2c3d ===\n"
    line, meta = dn._compose_message(text, BRANCH)
    assert "成功" in line
    assert meta["severity"] == SEVERITY_INFO
    assert "a1b2c3d" in meta["detail"]


def test_compose_message_rollback_keeps_branch_and_reason():
    text = (
        "[..] running checks…\n"
        "[..] ROLLBACK: checks failed — main restored to deadbee; "
        "branch dogfood/abcd1234 kept for debugging\n"
    )
    line, meta = dn._compose_message(text, BRANCH)
    assert "回滚" in line
    assert meta["severity"] == SEVERITY_ERROR
    assert BRANCH in meta["detail"]
    assert "checks failed" in meta["detail"]


def test_compose_message_conflict_is_not_reported_as_rollback():
    text = "[..] CONFLICT: dogfood/abcd1234 does not merge cleanly — resolve manually\n"
    line, meta = dn._compose_message(text, BRANCH)
    assert meta["severity"] == SEVERITY_ERROR
    assert BRANCH in meta["detail"]
    # 没合上和合上又退回来是两回事，说错了人就去查错的东西。
    assert "回滚" not in line and "回滚" not in meta["detail"]


def test_compose_message_lock_timeout():
    text = "[..] another push-back is still running after 600s — giving up\n"
    line, meta = dn._compose_message(text, BRANCH)
    assert meta["severity"] == SEVERITY_ERROR
    assert BRANCH in meta["detail"]


def test_compose_message_unrecognized_output_never_claims_success():
    text = "[..] SKIP: dev working tree is dirty — resolve manually: git merge x\n"
    line, meta = dn._compose_message(text, BRANCH)
    assert "成功" not in line
    assert meta["severity"] != SEVERITY_INFO
    assert "SKIP" in meta["detail"]  # falls back to the last non-empty log line


def test_compose_message_none_text_is_unknown_outcome():
    line, meta = dn._compose_message(None, BRANCH)
    assert "未知" in line
    assert BRANCH in meta["detail"]


# ---- _wait_for_output: process exit + offset-scoped log read --------------


@pytest.mark.anyio
async def test_wait_for_output_reads_only_bytes_after_offset(tmp_path):
    log, offset = _write_log(tmp_path, "stale prior run\n", "this run's output\n")
    text = await dn._wait_for_output(FakeProc(), log, offset)
    assert text == "this run's output\n"
    assert "stale" not in text


@pytest.mark.anyio
async def test_wait_for_output_times_out_on_a_hung_process(tmp_path, monkeypatch):
    monkeypatch.setattr(dn, "WAIT_TIMEOUT_SECONDS", 0.05)
    log, offset = _write_log(tmp_path, "", "won't be reached\n")
    text = await dn._wait_for_output(FakeProc(exit_after=5), log, offset)
    assert text is None


# ---- watch_dogfood_push: end-to-end wiring ---------------------------------


@pytest.mark.anyio
async def test_watch_dogfood_push_posts_success_to_the_right_topic(
    tmp_path, monkeypatch
):
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    sink = _wire_fake_db(monkeypatch, topic=FakeTopic(topic_id, project_id))
    log, offset = _write_log(
        tmp_path, "", "=== push-back done: platform now runs deadbee ===\n"
    )

    await dn.watch_dogfood_push(topic_id, FakeProc(), log, offset, "dogfood/abcd1234")

    assert len(sink) == 1
    call = sink[0]
    assert call["topic_id"] == topic_id
    assert call["project_id"] == project_id
    assert call["author_type"] == AuthorType.system
    assert call["kind"] == BlockKind.event
    assert "deadbee" in call["meta"]["detail"]


@pytest.mark.anyio
async def test_watch_dogfood_push_skips_silently_if_topic_is_gone(
    tmp_path, monkeypatch
):
    sink = _wire_fake_db(monkeypatch, topic=None)
    log, offset = _write_log(tmp_path, "", _DONE_LOG)

    await dn.watch_dogfood_push(
        uuid.uuid4(), FakeProc(), log, offset, "dogfood/abcd1234"
    )

    assert sink == []


@pytest.mark.anyio
async def test_watch_dogfood_push_retries_a_flaky_post_until_it_lands(
    tmp_path, monkeypatch
):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: real_sleep(0))
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    sink = _wire_fake_db(
        monkeypatch, topic=FakeTopic(topic_id, project_id), fail_times=2
    )
    log, offset = _write_log(tmp_path, "", _DONE_LOG)

    await dn.watch_dogfood_push(topic_id, FakeProc(), log, offset, "dogfood/abcd1234")

    assert len(sink) == 1  # the third attempt finally landed


@pytest.mark.anyio
async def test_watch_dogfood_push_gives_up_quietly_after_exhausting_retries(
    tmp_path, monkeypatch, caplog
):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: real_sleep(0))
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    sink = _wire_fake_db(
        monkeypatch, topic=FakeTopic(topic_id, project_id), fail_times=99
    )
    log, offset = _write_log(tmp_path, "", _DONE_LOG)

    # Must not raise — an undeliverable notice is a logged failure, not a crash.
    await dn.watch_dogfood_push(topic_id, FakeProc(), log, offset, "dogfood/abcd1234")

    assert sink == []


@pytest.mark.anyio
async def test_watch_dogfood_push_reports_unknown_when_proc_never_exits(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dn, "WAIT_TIMEOUT_SECONDS", 0.05)
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    sink = _wire_fake_db(monkeypatch, topic=FakeTopic(topic_id, project_id))
    log, offset = _write_log(tmp_path, "", "never read\n")

    await dn.watch_dogfood_push(
        topic_id, FakeProc(exit_after=5), log, offset, "dogfood/abcd1234"
    )

    assert len(sink) == 1
    assert "未知" in sink[0]["content"]
