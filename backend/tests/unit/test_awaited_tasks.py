"""后台任务唤醒：登记闸门与唤醒正文（不碰 DB 的那半）。

The wake decision itself (four guards, end to end over HTTP) lives in
tests/integration/test_await_wake.py — these cover what a registration refuses
and what the agent actually reads when it is woken.
"""

import uuid

import pytest

from app.core.errors import ConflictError, ValidationError
from app.domain.agent import awaited_tasks


@pytest.fixture(autouse=True)
def _clean_registry():
    awaited_tasks.reset()
    yield
    awaited_tasks.reset()


def _register(topic_id: uuid.UUID, command: str = "sleep 1", **kw):
    return awaited_tasks.register(
        project_id=kw.pop("project_id", uuid.uuid4()),
        topic_id=topic_id,
        command=command,
        label=kw.pop("label", ""),
        timeout_s=kw.pop("timeout_s", 600),
        log_path=kw.pop("log_path", "/home/node/.cheese/await/x.log"),
    )


def test_registered_task_is_tracked_for_its_topic():
    topic = uuid.uuid4()
    task = _register(topic, "bash check.sh")
    assert awaited_tasks.get(task.id) is task
    assert awaited_tasks.active_for_topic(topic) == [task]
    # A different topic's registry is untouched.
    assert awaited_tasks.active_for_topic(uuid.uuid4()) == []


def test_blank_label_falls_back_to_the_command():
    task = _register(uuid.uuid4(), "bash .claude/scripts/check.sh --full")
    assert task.label == "bash .claude/scripts/check.sh --full"


def test_empty_command_is_refused():
    with pytest.raises(ValidationError):
        _register(uuid.uuid4(), "   ")


@pytest.mark.parametrize("timeout_s", [0, -1, awaited_tasks.MAX_TIMEOUT_S + 1])
def test_out_of_range_timeout_is_refused(timeout_s):
    with pytest.raises(ValidationError):
        _register(uuid.uuid4(), timeout_s=timeout_s)


def test_a_topic_cannot_pile_up_background_tasks():
    """First storm brake: an agent looping on background work is stopped at
    registration, not after it has already spent a wake."""
    topic = uuid.uuid4()
    for _ in range(awaited_tasks.MAX_ACTIVE_PER_TOPIC):
        _register(topic)
    with pytest.raises(ConflictError):
        _register(topic)
    # Another topic is unaffected — the cap is per topic, not global.
    _register(uuid.uuid4())


def test_finished_task_frees_its_slot():
    topic = uuid.uuid4()
    tasks = [_register(topic) for _ in range(awaited_tasks.MAX_ACTIVE_PER_TOPIC)]
    awaited_tasks.forget(tasks[0].id)
    assert awaited_tasks.get(tasks[0].id) is None
    _register(topic)  # the freed slot is usable


def test_summary_carries_everything_needed_to_act():
    task = _register(uuid.uuid4(), "bash check.sh", label="全量检查")
    text = awaited_tasks.summary(
        task, exit_code=3, tail="FAILED tests/test_x.py::test_y", duration_s=2401.7
    )
    assert "全量检查" in text
    assert "退出码 3" in text
    assert "bash check.sh" in text  # what ran
    assert "2402s" in text  # how long
    assert task.log_path in text  # where the full output is
    assert "FAILED tests/test_x.py::test_y" in text  # the tail itself


def test_summary_says_timeout_rather_than_a_bare_exit_code():
    task = _register(uuid.uuid4(), "sleep 99999", timeout_s=60)
    text = awaited_tasks.summary(task, exit_code=124, tail="", duration_s=60.0)
    assert "超时" in text
    assert "60s" in text


def test_summary_clips_a_huge_tail():
    task = _register(uuid.uuid4())
    text = awaited_tasks.summary(task, exit_code=0, tail="x" * 50_000, duration_s=1.0)
    assert len(text) < awaited_tasks.TAIL_LIMIT + 1000
    assert "x" * 100 in text  # …but the end of the output survives


def test_summary_handles_a_silent_command():
    task = _register(uuid.uuid4())
    text = awaited_tasks.summary(task, exit_code=0, tail="", duration_s=0.5)
    assert "成功" in text
