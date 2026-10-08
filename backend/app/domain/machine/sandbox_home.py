"""What the platform does to one session's home on a cloud host.

Piped to ``python3 -`` on the host (``lifecycle.program``) after the source of
``agent/resource_cleanup.py``, whose helpers arrive as ``cleanup``: this file
runs there with nothing of ours importable, like that one.

- ``destroy``: end the session's sandbox and delete its home. Its executor is
  asked to stop, then whatever still holds the home — a dev server, a
  background command — is ended, and the home, the legacy work directory and
  the sandbox's temporary directory are removed. A home already gone is not
  an error.

Answers one line of JSON.
"""

import json
import sys
import time
from pathlib import Path


def destroy(cleanup, project, resource) -> dict:
    home, work = cleanup["resource_paths"](Path.home(), project, resource)
    if home.exists():
        cleanup["stop_executor"](home, resource)
    cleanup["end_holders"]([home, work])
    cleanup["check_no_writers"]([home, work])
    for path in (work, home, cleanup["resource_tmp"](resource)):
        if path.exists():
            cleanup["remove_tree"](path)
    # Last, as the room's cleanup does: until the home is gone, it is the
    # sandbox's to have written.
    cleanup["sandbox_marker"](home).unlink(missing_ok=True)
    return {"destroyed": True}


ACTIONS = {"destroy": destroy}


def main(cleanup, request):
    started = time.monotonic()
    answer = ACTIONS[request.pop("action")](cleanup, **request)
    answer["seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(answer))
    sys.stdout.flush()
