"""The sandbox-side `chat_send`: two calls, two different reads.

The executor journals every `invoke` by its request id for the whole session:
a second call with the same id and other input is refused, and with identical
input it replays the first answer. The tool therefore gives each call its own
prefix — without it, the second picture a session sends either fails or goes
out as the stale bytes of the first one.
"""

import base64
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def cheese_module():
    loader = SourceFileLoader("cheese_cli_chat_send", str(CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli_chat_send", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Host:
    """The one part of a session `chat_send` uses: its machine and the API."""

    environ = {"CHEESE_TOPIC": "topic-1", "CHEESE_PROJECT": "project-1"}

    def __init__(self, raw):
        self.raw = raw
        self.invoked = []
        self.published = []

    def invoke(self, payload, args):
        self.invoked.append(payload["id"])
        command = args["command"]
        if command.startswith("wc -c"):
            return {"value": {"stdout": str(len(self.raw))}}
        return {"value": {"stdout": base64.b64encode(self.raw).decode("ascii")}}

    def request(self, plan):
        self.published.append(plan)
        return {"data": {"id": "sent"}}


def test_each_call_reads_the_file_again_with_its_own_request_ids():
    """Two sends of the same path are two reads: a picture regenerated between
    them must go out as the new bytes, not the journal's first answer."""
    module = cheese_module()
    host = Host(b"first")
    module.run_platform_tool(
        "chat_send", {"content": "one", "attachments": ["/tmp/shot.png"]}, host
    )
    host.raw = b"second"
    module.run_platform_tool(
        "chat_send", {"content": "two", "attachments": ["/tmp/shot.png"]}, host
    )

    assert len(set(host.invoked)) == len(host.invoked), host.invoked
    sent = [plan["body"]["attachments"][0]["content_b64"] for plan in host.published]
    assert [base64.b64decode(item) for item in sent] == [b"first", b"second"]
