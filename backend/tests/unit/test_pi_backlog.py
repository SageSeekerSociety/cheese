"""Reading pi's turn from a cursor, including after the reader dies mid-turn.

Same recording as the translator's tests: one real GLM-5.2 turn that wrote,
read, edited and ran a command.
"""

import json
from pathlib import Path

from app.domain.agent.harness.pi.backlog import PiBacklog
from app.domain.agent.harness.pi.journal import Journal
from app.domain.agent.service import AgentResult, AgentToolUse

ENTRIES = json.loads((Path(__file__).parent / "fixtures/pi-entries.json").read_text())[
    "entries"
]


def mirrored(tmp_path, entries=None):
    path = tmp_path / "entries.sqlite"
    journal = Journal(path)
    try:
        journal.import_entries(entries if entries is not None else ENTRIES)
    finally:
        journal.close()
    return path


def drain(path, session_id="session-1"):
    backlog = PiBacklog(path, session_id)
    events = []
    for entry in backlog.unread():
        events.extend(backlog.assemble(entry))
    return backlog, events


def test_asking_pi_for_the_same_page_again_lands_nothing_twice(tmp_path):
    path = mirrored(tmp_path)
    journal = Journal(path)
    try:
        journal.import_entries(ENTRIES)
        journal.import_entries(ENTRIES[3:])
        assert len(journal.read()) == len(ENTRIES)
        # The cursor to ask pi from is the last entry we actually hold.
        assert journal.recall("received") == ENTRIES[-1]["id"]
    finally:
        journal.close()


def test_nothing_is_ever_half_arrived(tmp_path):
    backlog, _ = drain(mirrored(tmp_path))
    assert backlog.unfinished() == set()
    assert backlog.give_up() == []


def test_a_reader_that_died_midway_resumes_and_still_bills_the_whole_turn(tmp_path):
    path = mirrored(tmp_path)
    whole, complete = drain(path)
    assert len(whole.unread()) == len(ENTRIES)

    # A pass that landed the first half and then died.
    partial = PiBacklog(path, "session-1")
    half = partial.unread()[:5]
    for entry in half:
        partial.assemble(entry)
    partial.landed(through=half[-1].key)

    resumed, rest = drain(path)
    assert len(resumed.unread()) == len(ENTRIES) - 5

    # The turn ends once, in the second pass, and its bill covers the messages
    # the first pass already landed — otherwise a restart bills a fraction.
    finished = [event for event in rest if isinstance(event, AgentResult)]
    assert len(finished) == 1
    expected = next(event for event in complete if isinstance(event, AgentResult))
    assert finished[0].usage is not None and expected.usage is not None
    assert finished[0].usage.cost_usd == expected.usage.cost_usd
    assert finished[0].usage.total_tokens == expected.usage.total_tokens


def test_what_the_first_pass_reported_is_reported_again_under_the_same_id(tmp_path):
    """Reported is not landed. Anything the platform did not acknowledge comes
    back, and the id is what keeps that from becoming a duplicate."""
    path = mirrored(tmp_path)
    _, first = drain(path)
    _, second = drain(path)
    tools = [e.eid for e in first if isinstance(e, AgentToolUse)]
    assert tools and tools == [e.eid for e in second if isinstance(e, AgentToolUse)]


def test_retention_removes_an_entry_never_having_been_read(tmp_path):
    path = mirrored(tmp_path)
    backlog, _ = drain(path)
    backlog.forget(older_than_s=0)
    assert len(PiBacklog(path, "session-1").unread()) == len(ENTRIES), (
        "an unlanded entry must survive retention"
    )
    backlog.landed(through=backlog.unread()[-1].key)
    backlog.forget(older_than_s=0)
    assert PiBacklog(path, "session-1").unread() == []
