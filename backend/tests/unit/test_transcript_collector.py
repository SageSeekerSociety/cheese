"""Run the device collector against files and an actual HTTP receiver."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from app.domain.agent.event_drain import collect_transcripts


@pytest.fixture
def receiver():
    saved = {}
    failures = []
    confirmations = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_PUT(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            content = self.rfile.read(int(self.headers["Content-Length"]))
            offset = int(query["offset"][0])
            key = (url.path.rsplit("/", 1)[-1], offset)
            if key in saved:
                assert saved[key] == content
            saved[key] = content
            if failures:
                failures.pop()
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "offset": offset,
                            "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        },
                    }
                ).encode()
            )

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            file_id = urlparse(self.path).path.split("/")[-2]
            confirmations.append((file_id, body["offset"]))
            content = saved.get((file_id, body["offset"]), b"")
            if (
                failures
                or len(content) != body["size"]
                or hashlib.sha256(content).hexdigest() != body["sha256"]
            ):
                if failures:
                    failures.pop()
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"code": 200, "data": body}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield (
        f"http://127.0.0.1:{server.server_port}/sandbox/hooks/topic",
        saved,
        failures,
        confirmations,
    )
    server.shutdown()
    server.server_close()
    thread.join()


def test_retry_restart_delayed_tail_and_subagent_preserve_original_bytes(
    tmp_path, receiver
):
    url, saved, failures, _ = receiver
    home = tmp_path / "home"
    root = home / ".claude/projects/p"
    root.mkdir(parents=True)
    main = root / "main.jsonl"
    main.write_bytes("你好\n".encode())
    home.joinpath(".cheese").mkdir(exist_ok=True)
    script = home / ".cheese/cheese-drain"
    values = {
        "CHEESE_HOOK_SPOOL": str(home / ".cheese/cheese-spool"),
        "CHEESE_HOOK_URL": url,
        "CHEESE_TOKEN": "test",
    }
    failures.append(True)
    with pytest.raises(RuntimeError, match="offset=0"):
        collect_transcripts(script, values)
    # The receiver stored it but the acknowledgement was lost. A new collector
    # invocation must retry the same file identity and byte range.
    with main.open("ab") as output:
        output.write(b"delayed final response\n")
    collect_transcripts(script, values)
    assert len(saved) == 2
    sub = root / "main/subagents/a.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_bytes(b"subagent original\n")
    receipts = collect_transcripts(script, values, flush=True)
    assert len(receipts) == 2
    for receipt in receipts:
        content = b"".join(
            value
            for (file_id, offset), value in sorted(saved.items())
            if file_id == receipt["id"]
        )
        assert content == (home / receipt["source"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == receipt["sha256"]


def test_replaced_or_rewritten_file_gets_a_new_generation(tmp_path, receiver):
    url, saved, _, _ = receiver
    root = tmp_path / ".claude/projects/p"
    root.mkdir(parents=True)
    path = root / "s.jsonl"
    tmp_path.joinpath(".cheese").mkdir(exist_ok=True)
    script = tmp_path / ".cheese/cheese-drain"
    values = {
        "CHEESE_HOOK_SPOOL": str(tmp_path / ".cheese/cheese-spool"),
        "CHEESE_HOOK_URL": url,
        "CHEESE_TOKEN": "test",
    }
    path.write_bytes(b"first\n")
    before = collect_transcripts(script, values, flush=True)[0]
    path.write_bytes(b"rewritten\n")
    after = collect_transcripts(script, values, flush=True)[0]
    assert before["id"] != after["id"]
    assert saved[(before["id"], 0)] == b"first\n"
    assert saved[(after["id"], 0)] == b"rewritten\n"


def test_final_verification_resumes_chunks_but_new_cleanup_checks_them_again(
    tmp_path, receiver, monkeypatch
):
    from app.domain.agent import event_drain

    url, saved, _, confirmations = receiver
    root = tmp_path / ".claude/projects/p"
    root.mkdir(parents=True)
    (root / "s.jsonl").write_bytes(b"original transcript\n")
    tmp_path.joinpath(".cheese").mkdir(exist_ok=True)
    script = tmp_path / ".cheese/cheese-drain"
    values = {
        "CHEESE_HOOK_SPOOL": str(tmp_path / ".cheese/cheese-spool"),
        "CHEESE_HOOK_URL": url,
        "CHEESE_TOKEN": "test",
        "CHEESE_CLEANUP_ID": "first",
    }
    monkeypatch.setattr(event_drain, "CHUNK_BYTES", 5)
    execute = event_drain.subprocess.run
    posts = 0

    def interrupted(command, **kwargs):
        nonlocal posts
        if "POST" in command:
            posts += 1
            if posts == 2:
                raise TimeoutError("worker restarted during final verification")
        return execute(command, **kwargs)

    monkeypatch.setattr(event_drain.subprocess, "run", interrupted)
    with pytest.raises(TimeoutError):
        collect_transcripts(script, values, flush=True)
    assert len(confirmations) == 1
    receipts = collect_transcripts(script, values, flush=True)
    assert len(confirmations) == len(saved)
    assert receipts[0]["sha256"] == hashlib.sha256(b"original transcript\n").hexdigest()
    values["CHEESE_CLEANUP_ID"] = "second"
    collect_transcripts(script, values, flush=True)
    assert len(confirmations) == 2 * len(saved)
