"""A document agent's reply to a comment thread (`document/thread.reply`): a
thread that moved on meanwhile is read again once; a second move drops the
reply, a vanished document drops it at once, anything else is not retried."""

import uuid

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.domain.agent.document import thread
from app.domain.agent.document.question import Asked, Bound
from app.domain.living_doc.comments import OnDocument


class _Db:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.log.append("commit")

    async def rollback(self):
        self.log.append("rollback")


def _wire(monkeypatch, outcomes):
    """`outcomes`: what each attempt's write does — None to land, or an
    exception to raise."""
    log: list = []
    pending = list(outcomes)

    class Documents:
        def __init__(self, db):
            pass

        async def get(self, document_id):
            return type("Doc", (), {"id": document_id})()

    class CommentThreads:
        def __init__(self, db):
            pass

        async def root(self, doc_id, thread_id):
            return thread_id

        async def describe(self, root):
            return {"revision": 1}

        async def mutate(self, doc, **kwargs):
            log.append("mutate")
            outcome = pending.pop(0)
            if outcome is not None:
                raise outcome

    async def tell(document_id, frame):
        log.append("tell")

    monkeypatch.setattr(thread, "Documents", Documents)
    monkeypatch.setattr(thread, "CommentThreads", CommentThreads)
    monkeypatch.setattr(thread.collab, "tell", tell)
    return log, (lambda: _Db(log))


async def _reply(factory):
    await thread.reply(
        factory,
        Asked(uuid.uuid4(), OnDocument(uuid.uuid4()), None, None),
        uuid.uuid4(),
        Bound("cheese-x", "芝士", "m", "m", "s"),
        "hi {agent}",
    )


@pytest.mark.anyio
async def test_a_thread_that_moved_on_is_read_again_once(monkeypatch):
    log, factory = _wire(monkeypatch, [ConflictError("moved"), None])
    await _reply(factory)
    assert log == ["mutate", "rollback", "mutate", "commit", "tell"]


@pytest.mark.anyio
async def test_a_second_move_drops_the_reply(monkeypatch, caplog):
    log, factory = _wire(monkeypatch, [ConflictError("a"), ConflictError("b")])
    with caplog.at_level("INFO", logger=thread.logger.name):
        await _reply(factory)
    assert log == ["mutate", "rollback", "mutate", "rollback"]
    assert any("reply dropped" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_a_vanished_document_drops_the_reply_at_once(monkeypatch):
    log, factory = _wire(monkeypatch, [NotFoundError("gone")])
    await _reply(factory)
    assert log == ["mutate"]


@pytest.mark.anyio
async def test_any_other_failure_is_not_retried(monkeypatch):
    log, factory = _wire(monkeypatch, [RuntimeError("boom"), None])
    with pytest.raises(RuntimeError, match="boom"):
        await _reply(factory)
    assert log == ["mutate"]
