"""The ping is the handshake every release reads, whatever protocol it speaks.

A runner outlives the release that started it, so the next release meets it
speaking a lifecycle protocol it does not read (``harness/driven/lifecycle.py``).
What it still reads is the ping: whether the runner is alive, which protocol and
launch it is, its conversation, whether it is at work and on what, and the
registration it holds, and how the work it last ended ended. That is how it lets
the work finish before replacing the runner, knows whether that work failed, and
interrupts the work under the runner's own registration once the drain runs
out. A runner of this release is held to those fields here, the
real archive against the pinned build, and to the busy flag being true for the
whole of a piece of work, a tool running included, and false once it ended.
"""

import uuid

from app.domain.agent.harness.driven import lifecycle
from tests.unit.test_claude_runner import (  # noqa: F401  fixtures
    Screen,
    _is,
    _status,
    contract,
    machine,
    screen,
)

#: The handshake: each field and the types its value may take. A later protocol
#: may add fields to the ping, never drop or change these.
HANDSHAKE = {
    "alive": (bool,),
    "lifecycle": (int,),
    "launch": (str,),
    "session_id": (str,),
    "working": (bool,),
    "work_id": (str, type(None)),
    "worker_epoch": (int,),
    "endings": (list,),
}


def _handshake(ping: dict) -> dict:
    for field, types in HANDSHAKE.items():
        assert field in ping, f"the ping dropped {field!r}: {ping}"
        assert isinstance(ping[field], types), (field, ping[field])
    return {field: ping[field] for field in HANDSHAKE}


def test_the_ping_says_whether_the_runner_is_at_work_from_start_to_end(
    screen,  # noqa: F811
    contract,  # noqa: F811
):
    idle = _handshake(screen.call("ping"))
    assert idle["alive"] is True
    assert idle["lifecycle"] == lifecycle.PROTOCOL
    assert idle["launch"]
    assert idle["working"] is False and idle["work_id"] is None

    work = str(uuid.uuid4())
    sent = screen.send(
        contract.do("Bash", command="sleep 4; echo DONE", description="wait"),
        work_id=work,
    )
    # The model called the tool and it is running: the work is under way, and
    # the ping says whose.
    screen.wait(_is("assistant"))
    busy = _handshake(screen.call("ping"))
    assert busy["working"] is True and busy["work_id"] == work
    assert busy["worker_epoch"] == Screen.EPOCH
    assert busy["session_id"] and busy["launch"] == idle["launch"]

    screen.wait_event(_status("idle", work))
    done = _handshake(screen.call("ping"))
    assert done["working"] is False and done["work_id"] is None
    assert done["alive"] is True
    # How it ended: cleanly, having read what it was sent.
    assert done["endings"][-1] == {"work": work, "failure": None, "inputs": [sent]}

    # Work taken away ends in a failure, and the ping says so.
    cut = str(uuid.uuid4())
    screen.send(
        contract.do("Bash", command="sleep 30", description="wait"), work_id=cut
    )
    screen.wait(_is("assistant"), after=screen.records()[-1]["sequence"])
    screen.call("interrupt")
    screen.wait_event(_status("idle", cut))
    stopped = _handshake(screen.call("ping"))
    assert stopped["endings"][-1]["work"] == cut
    assert stopped["endings"][-1]["failure"] == {"kind": "interrupted"}
    assert [ending["work"] for ending in stopped["endings"]] == [work, cut]
