"""How Claude Code work ends, as the room hears it: the build's records played
through the real runner, whose report of the session going idle is turned into
the room's result the way the platform's reading turns it
(`harness/driven/reporting.result_of`)."""

import tempfile
import uuid
from pathlib import Path

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.events import Assembler
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.driven import lifecycle
from app.domain.agent.harness.driven.reporting import Whose, result_of
from app.domain.agent.service import AgentResult


def work_ended(
    seat: str, *records: dict, session_id: str = "native-session"
) -> AgentResult:
    """The result the room hears for one piece of work whose records are
    ``records`` (the last one its ``result``), run by ``seat``."""
    with tempfile.TemporaryDirectory() as state:
        runner = Runner(Path(state))
        try:
            runner.outbox.register(1)
            runner.session_id = session_id
            runner._open(("send", str(uuid.uuid4())))
            for record in records:
                runner.observe({"uuid": str(uuid.uuid4()), **record})
            (idle,) = [
                event
                for event in runner.outbox.pending()
                if event["kind"] == lifecycle.STATUS
                and event["worker_status"] == lifecycle.IDLE
            ]
        finally:
            runner.journal.close()
    ref = SessionRef(uuid.uuid4(), uuid.uuid4(), seat, harness=CLAUDE_CODE)
    whose = Whose(ref, seat, ref.topic_id, seat, CLAUDE_CODE, "Claude Code")
    result, _kind = result_of(whose, idle["result"], session_id=session_id)
    return result


def translated(records: list[dict], session_id: str = "session-1") -> list:
    """What the room gets from Claude Code's ``records``, in order: each record
    the runner journaled through the platform's assembler, and, where a
    ``result`` ended the work, the result the runner's report of the session
    going idle comes out as. One piece of work, opened as a send opens it."""
    events: list = []
    with tempfile.TemporaryDirectory() as state:
        runner = Runner(Path(state))
        try:
            runner.outbox.register(1)
            runner.session_id = session_id
            runner._open(("send", str(uuid.uuid4())))
            assembler = Assembler(session_id)
            ref = SessionRef(uuid.uuid4(), uuid.uuid4(), "", harness=CLAUDE_CODE)
            whose = Whose(ref, "", ref.topic_id, "", CLAUDE_CODE, "Claude Code")
            for record in records:
                seen = runner.journal.last()
                acked = runner.outbox.last()
                runner.observe(record)
                for row in runner.journal.read(seen):
                    if row["record"].get("type") != "result":
                        events += assembler.accept(row["record"])
                for event in runner.outbox.pending():
                    if (
                        event["seq"] > acked
                        and event["kind"] == lifecycle.STATUS
                        and event["worker_status"] == lifecycle.IDLE
                    ):
                        result, _ = result_of(
                            whose, event["result"], session_id=event["session_id"]
                        )
                        events.append(result)
        finally:
            runner.journal.close()
    return events
