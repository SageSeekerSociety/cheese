"""Sync hook outcomes remain visible only when the task push fails."""


def test_a_failed_sync_becomes_something_the_human_sees():
    """The report is only worth sending if it reaches a person.

    A turn whose work never left the machine looks exactly like one that
    succeeded — same assistant reply, same completed turn. That resemblance is
    the bug; it let a rejected push pass for delivered work until the machine
    was deleted and the work went with it.
    """
    from app.domain.agent.harness.claude_code.hook_events import translate_hook
    from app.domain.agent.service import AgentMessage

    failed = translate_hook(
        {"hook_event_name": "CheeseSync", "status": "failed", "branch": "topic/abc"}
    )

    assert isinstance(failed, AgentMessage), "a failed sync produced nothing visible"
    assert "topic/abc" in failed.text
    assert "机器" in failed.text, "it must say where the work actually is"


def test_a_successful_sync_stays_quiet():
    """Success needs no message — the work is in the branch already, and a
    notice every turn is noise that teaches people to ignore the warning."""
    from app.domain.agent.harness.claude_code.hook_events import translate_hook

    assert (
        translate_hook(
            {"hook_event_name": "CheeseSync", "status": "ok", "commit": "deadbeef"}
        )
        is None
    )
