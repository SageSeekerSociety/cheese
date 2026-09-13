"""Concurrent retained histories advance independent durable cursors."""

from app.domain.agent.harness.backlog import CombinedBacklog
from app.domain.agent.harness.codex.backlog import CodexBacklog
from app.domain.agent.harness.codex.journal import Journal


def test_landing_one_history_does_not_consume_the_other(tmp_path):
    paths = [tmp_path / "first", tmp_path / "second"]
    for index, path in enumerate(paths):
        journal = Journal(path)
        journal.append(
            {
                "method": "item/completed",
                "params": {
                    "threadId": f"thread-{index}",
                    "item": {
                        "id": "reply",
                        "type": "agentMessage",
                        "text": f"answer {index}",
                    },
                },
            }
        )
        journal.close()
    backlog = CombinedBacklog([CodexBacklog(path) for path in paths])
    first, second = backlog.unread()
    assert first.key != second.key
    assert backlog.assemble(first)[0].text == "answer 0"
    backlog.landed(through=first.key)
    assert not CodexBacklog(paths[0]).unread()
    assert len(CodexBacklog(paths[1]).unread()) == 1
    assert backlog.assemble(second)[0].text == "answer 1"
    backlog.landed(through=second.key)
    assert not CodexBacklog(paths[1]).unread()
