"""A stand-in for `pi --mode rpc`: the protocol, none of the model.

Speaks what the runner actually depends on — JSONL on stdio, a reply carrying
the request id, bare events with none, and `get_entries` answering from a
cursor. What it replays is the recorded entries of a real GLM-5.2 turn, so a
test drives the runner over the same shapes a machine would produce.

Deliberately not a mock inside the test process: the runner owns a subprocess,
its pipes and its framing, and none of that is exercised by a fake object.
"""

import json
import sys
import time
from pathlib import Path


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(request, kind, data=None, success=True):
    emit(
        {
            "id": request,
            "type": "response",
            "command": kind,
            "success": success,
            "data": data or {},
        }
    )


def main() -> None:
    entries = json.loads(Path(sys.argv[1]).read_text())["entries"]
    # `--session-id <id>` is how the platform names the session; echo it back
    # through get_session_stats so a test can see the id it asked for.
    arguments = sys.argv[2:]
    session_id = (
        arguments[arguments.index("--session-id") + 1]
        if "--session-id" in arguments
        else "unnamed"
    )
    produced: list[dict] = []
    prompts: list[str] = []

    for line in sys.stdin:
        line = line.rstrip("\r\n")
        if not line:
            continue
        command = json.loads(line)
        kind, request = command.get("type"), command.get("id")

        if kind == "get_entries":
            since = command.get("since")
            if since is None:
                page = list(produced)
            else:
                ids = [entry["id"] for entry in produced]
                page = produced[ids.index(since) + 1 :] if since in ids else []
            leaf = produced[-1]["id"] if produced else None
            reply(request, kind, {"entries": page, "leafId": leaf})
        elif kind in ("prompt", "steer"):
            prompts.append(command.get("message", ""))
            reply(request, kind)
            emit({"type": "agent_start"})
            produced.extend(entries)
            # The doorbell the runner listens for; the record is get_entries.
            emit({"type": "agent_end"})
            emit({"type": "agent_settled"})
        elif kind == "abort":
            reply(request, kind)
            emit({"type": "agent_settled"})
        elif kind == "get_session_stats":
            reply(request, kind, {"sessionId": session_id, "prompts": len(prompts)})
        else:
            reply(request, kind, success=False)
        time.sleep(0)


if __name__ == "__main__":
    main()
