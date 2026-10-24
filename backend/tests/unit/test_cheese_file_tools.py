"""A person's 芝士 moves a file between its computer and the person's library
by having the computer itself put it to, or fetch it from, the address the
platform gives for that one file (`cheese_file_give`,
`cheese_file_to_computer`): the bytes go straight between the computer and
the storage, and the computer holds no credential.

The computer here is this machine's shell in a scratch directory, and the
storage is a small HTTP server that keeps what is PUT to it and serves it
back: what lands on either side is what is checked.
"""

import importlib.util
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_file_tools", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


@pytest.fixture
def storage():
    """Keeps what is PUT at a path; answers a GET with it."""
    kept: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            kept[self.path] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            if self.path not in kept:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(kept[self.path])))
            self.end_headers()
            self.wfile.write(kept[self.path])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", kept
    server.shutdown()


class Computer:
    """The session's host: the platform's answers, and a shell in ``home``
    for what runs on the computer."""

    def __init__(self, home: Path, answers: dict):
        self.home = home
        self.answers = answers
        self.environ: dict = {}
        self.asked: list[dict] = []

    def request(self, plan):
        self.asked.append(plan)
        return {"data": self.answers[plan["path"]](plan.get("body") or {})}

    def invoke(self, payload, args):
        done = subprocess.run(
            ["bash", "-c", args["command"]],
            cwd=self.home,
            capture_output=True,
            text=True,
            timeout=args["timeout"] / 1000,
        )
        out = done.stdout + done.stderr
        return {
            "value": {
                "stdout": out
                if done.returncode == 0
                else f"Exit code {done.returncode}\n{out}"
            }
        }


def test_a_file_made_on_the_computer_is_put_to_its_address_and_handed_over(
    tmp_path, storage
):
    origin, kept = storage
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "成绩分布.png").write_bytes(b"PNG" * 1000)
    computer = Computer(
        tmp_path,
        {
            "/assistant/agent/files/slot": lambda body: {
                "url": f"{origin}/slot-1",
                "upload": "sealed-1",
            },
            "/assistant/agent/files/handed": lambda body: {
                "path": body["name"],
                "bytes": 3000,
            },
        },
    )

    said = cheese.run_platform_tool(
        "cheese_file_give",
        {"path": "out/成绩分布.png", "name": "数据结构/成绩分布.png"},
        computer,
    )

    assert kept["/slot-1"] == b"PNG" * 1000
    slot, handed = computer.asked
    assert slot["body"] == {"name": "成绩分布.png", "size": 3000}
    assert handed["body"] == {"upload": "sealed-1", "name": "数据结构/成绩分布.png"}
    assert "library/数据结构/成绩分布.png" in said


def test_a_file_not_on_the_computer_is_said_so_and_nothing_is_asked(tmp_path):
    computer = Computer(tmp_path, {})
    with pytest.raises(cheese.PlatformToolError, match="没有这份文件"):
        cheese.run_platform_tool("cheese_file_give", {"path": "nope.png"}, computer)
    assert computer.asked == []


def test_a_file_of_the_persons_is_fetched_onto_the_computer(tmp_path, storage):
    origin, kept = storage
    kept["/file-1"] = b"name,score\nan,92\n"
    computer = Computer(
        tmp_path,
        {
            "/assistant/agent/files/link": lambda body: {
                "url": f"{origin}/file-1",
                "path": "成绩.csv",
                "bytes": 17,
            }
        },
    )

    said = cheese.run_platform_tool(
        "cheese_file_to_computer", {"ref": "library/成绩.csv"}, computer
    )

    assert (tmp_path / "files" / "成绩.csv").read_bytes() == b"name,score\nan,92\n"
    assert "files/成绩.csv" in said


def test_a_failed_transfer_is_an_error_not_a_file(tmp_path, storage):
    origin, _ = storage
    computer = Computer(
        tmp_path,
        {
            "/assistant/agent/files/link": lambda body: {
                "url": f"{origin}/gone",
                "path": "a.txt",
                "bytes": 1,
            }
        },
    )
    with pytest.raises(cheese.PlatformToolError):
        cheese.run_platform_tool(
            "cheese_file_to_computer",
            {"ref": "library/a.txt", "path": "a.txt"},
            computer,
        )
    assert not (tmp_path / "a.txt").exists()
