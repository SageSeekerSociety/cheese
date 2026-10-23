"""A platform that has said no for good, or keeps failing, is not asked again.

Two incidents on dev, both answered by agents retrying every second or two for
hours: a room turned into a task left its sessions holding credentials the
platform refused for good (403, ``retryable: false``), and a backend that
answered 500 to every lease. The remote-execution client never read either.
"""

import json

import pytest

from app.domain.agent import executor_transport
from app.domain.agent.executor_transport import (
    PLATFORM_FAILING,
    SESSION_ENDED,
    RemoteClient,
)

# The body the platform answers with (`format_error_response`).
_REFUSED = {
    "code": 403,
    "message": "Execution credential does not own this session",
    "error": {
        "name": "ForbiddenError",
        "message": "Execution credential does not own this session",
        "data": None,
        "retryable": False,
    },
}
#: The task credential's refusal, said as a catalog sentence: the words are
#: Chinese and the reader's language, so only the key is stable
#: (`app/core/errors.py`).
_REFUSED_IN_WORDS = {
    "code": 403,
    "message": "任务里的 AI 队友只在自己那段对话里读写。…",
    "error": {
        "name": "ForbiddenError",
        "message": "任务里的 AI 队友只在自己那段对话里读写。…",
        "data": None,
        "retryable": False,
        "i18n": {"key": "taskCredentialOtherConversation", "params": {}},
    },
}
_LEASE = {
    "data": {
        "target": {"kind": "device", "url": "http://executor.test"},
        "token": "execution-only",
    }
}


class Platform:
    """Answers each request with the next of ``answers``: (status, body)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = 0

    def install(self, monkeypatch):
        platform = self

        class Connection:
            def request(self, method, path, *, body, headers):
                platform.asked += 1
                self.status, self.body = platform.answers.pop(0)

            def getresponse(self):
                return self

            def read(self):
                return json.dumps(self.body).encode()

            def close(self):
                pass

        def connection(self):
            self.transport.headers = {}
            return Connection(), ""

        monkeypatch.setattr(RemoteClient, "connection", connection)
        monkeypatch.setenv("CHEESE_API", "http://platform.test")


def _session(tmp_path, token="execution-only"):
    (tmp_path / "execution.token").write_text(token)
    return {
        "kind": "device",
        "url": "http://executor.test",
        "lease_path": "/lease",
        "target_file": str(tmp_path / "execution.json"),
        "token_file": str(tmp_path / "execution.token"),
    }


def test_a_credential_refused_for_good_ends_the_sessions_machine_calls(
    tmp_path, monkeypatch
):
    platform = Platform([(403, _REFUSED)])
    platform.install(monkeypatch)
    config = _session(tmp_path)
    with pytest.raises(RuntimeError) as first:
        RemoteClient(dict(config)).acquire(deadline=0)
    assert str(first.value) == SESSION_ENDED
    # The next command is another process, as each Bash call is: it reads what
    # the first one learned and does not ask the platform again.
    with pytest.raises(RuntimeError) as again:
        RemoteClient(dict(config)).acquire(deadline=0)
    assert str(again.value) == SESSION_ENDED
    assert platform.asked == 1


def test_a_catalog_sentence_ends_the_session_by_its_key(tmp_path, monkeypatch):
    """The refusal's words are the reader's language; the key does not change.

    A task's credential reaching another conversation is said as a catalog
    sentence now, and a leave note that only matched the English it used to say
    would have let the session ask again every two seconds.
    """
    platform = Platform([(403, _REFUSED_IN_WORDS)])
    platform.install(monkeypatch)
    config = _session(tmp_path)
    with pytest.raises(RuntimeError) as refused:
        RemoteClient(dict(config)).acquire(deadline=0)
    assert str(refused.value) == SESSION_ENDED
    assert platform.asked == 1


def test_a_new_credential_for_the_session_is_tried(tmp_path, monkeypatch):
    platform = Platform([(403, _REFUSED), (200, _LEASE)])
    platform.install(monkeypatch)
    config = _session(tmp_path)
    with pytest.raises(RuntimeError):
        RemoteClient(dict(config)).acquire(deadline=0)
    _session(tmp_path, token="relaunched")
    RemoteClient(dict(config)).acquire(deadline=0)
    assert platform.asked == 2


@pytest.mark.parametrize(
    "body",
    [
        {**_REFUSED, "error": {**_REFUSED["error"], "retryable": True}},
        {"code": 403, "message": "ForbiddenError: 只有房间成员可以这样做"},
    ],
)
def test_another_refusal_is_reported_and_asked_again(tmp_path, monkeypatch, body):
    platform = Platform([(403, body), (403, body)])
    platform.install(monkeypatch)
    config = _session(tmp_path)
    for _ in range(2):
        with pytest.raises(executor_transport.PlatformHTTPError):
            RemoteClient(dict(config)).acquire(deadline=0)
    assert platform.asked == 2


def test_three_failures_in_a_row_hold_the_platform_off_for_a_while(
    tmp_path, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(executor_transport.time, "time", lambda: clock[0])
    error = {"code": 500, "message": "服务器内部错误"}
    platform = Platform([(500, error)] * 4 + [(200, {})] + [(500, error)])
    platform.install(monkeypatch)
    config = _session(tmp_path)

    def ask():
        return RemoteClient(dict(config)).platform_request({"path": "/topics/room"})

    for _ in range(3):
        with pytest.raises(executor_transport.PlatformHTTPError):
            ask()
    with pytest.raises(RuntimeError) as held:
        ask()
    assert str(held.value) == PLATFORM_FAILING.format(seconds=30)
    assert platform.asked == 3

    # Past the window one request goes out; failing again holds twice as long.
    clock[0] += 31
    with pytest.raises(executor_transport.PlatformHTTPError):
        ask()
    with pytest.raises(RuntimeError) as longer:
        ask()
    assert str(longer.value) == PLATFORM_FAILING.format(seconds=60)

    # An answer that is not a failure clears it: the next failure is one again.
    clock[0] += 61
    ask()
    with pytest.raises(executor_transport.PlatformHTTPError):
        ask()
    assert platform.asked == 6


def test_a_front_that_gave_up_waiting_is_not_a_failure(tmp_path, monkeypatch):
    platform = Platform([(504, {})] * 4)
    platform.install(monkeypatch)
    client = RemoteClient(_session(tmp_path))
    for _ in range(4):
        with pytest.raises(executor_transport.PlatformHTTPError):
            client.platform_request({"path": "/topics/room"})
    assert platform.asked == 4


def test_a_session_without_a_target_file_keeps_the_count_in_memory(monkeypatch):
    """Codex and pi keep one client for the whole session."""
    platform = Platform([(500, {})] * 3)
    platform.install(monkeypatch)
    monkeypatch.setenv("CHEESE_TOKEN", "session")
    client = RemoteClient({"kind": "device", "url": "http://executor.test"})
    for _ in range(3):
        with pytest.raises(executor_transport.PlatformHTTPError):
            client.platform_request({"path": "/topics/room"})
    with pytest.raises(RuntimeError) as held:
        client.platform_request({"path": "/topics/room"})
    assert str(held.value).startswith(PLATFORM_FAILING.split("{")[0])
    assert platform.asked == 3


def test_one_route_failing_does_not_hold_off_the_others(tmp_path, monkeypatch):
    """A route can answer 503 on a healthy platform (the sandbox it needs was
    taken back): the rest of the session's platform calls still go out."""
    error = {"code": 503, "message": "上游不可用"}
    platform = Platform([(503, error)] * 3 + [(200, {})])
    platform.install(monkeypatch)
    config = _session(tmp_path)
    for _ in range(3):
        with pytest.raises(executor_transport.PlatformHTTPError):
            RemoteClient(dict(config)).platform_request({"path": "/projects/p/files"})
    with pytest.raises(RuntimeError) as held:
        RemoteClient(dict(config)).platform_request({"path": "/projects/p/files"})
    assert str(held.value) == PLATFORM_FAILING.format(seconds=30)
    RemoteClient(dict(config)).platform_request({"path": "/topics/room"})
    assert platform.asked == 4
