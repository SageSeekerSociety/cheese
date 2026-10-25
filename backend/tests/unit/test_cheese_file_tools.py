"""A person's 芝士 moves a file between its computer and the person's library
by having the computer itself put it to, or fetch it from, the address the
platform gives for that one file (`cheese_library(action=give)`,
`cheese_library(action=take)`): the bytes go straight between the computer and
the storage, and the computer holds no credential.

The computer here is this machine's shell in a scratch directory, and the
storage is a small HTTP server that keeps what is PUT to it and serves it
back: what lands on either side is what is checked.
"""

import hashlib
import importlib.util
import json
import socket
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
    """Keeps what is PUT at a path, refusing bytes that are not the MD5 the
    PUT names, as the bucket does; answers a GET with it."""
    kept: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            named = self.headers.get("Content-MD5")
            if named is not None and named != hashlib.md5(body).hexdigest():
                self.send_response(400)
                self.end_headers()
                return
            kept[self.path] = body
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
    """The computer cuts the file into the parts the platform asks for, puts
    each at its own address with its MD5, and hands the file over with the
    SHA-256 of the whole and the MD5 of each part."""
    origin, kept = storage
    content = bytes(range(256)) * 10 + b"tail"
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "成绩分布.png").write_bytes(content)
    computer = Computer(
        tmp_path,
        {
            "/assistant/agent/files/slot": lambda body: {
                "upload": "sealed-1",
                "part_bytes": 1024,
                "parts": 3,
            },
            "/assistant/agent/files/part": lambda body: {
                "url": f"{origin}/slot-1/{body['part']}",
                "headers": {"Content-MD5": body["md5"]},
            },
            "/assistant/agent/files/handed": lambda body: {
                "path": body["name"],
                "bytes": len(content),
            },
        },
    )

    said = cheese.run_platform_tool(
        "cheese_library",
        {"action": "give", "path": "out/成绩分布.png", "name": "数据结构/成绩分布.png"},
        computer,
        delegated=True,
    )

    assert b"".join(kept[f"/slot-1/{n}"] for n in (1, 2, 3)) == content
    handed = computer.asked[-1]["body"]
    assert handed["upload"] == "sealed-1"
    assert handed["sha256"] == hashlib.sha256(content).hexdigest()
    assert handed["parts"] == [
        hashlib.md5(content[i : i + 1024]).hexdigest() for i in (0, 1024, 2048)
    ]
    assert "library/数据结构/成绩分布.png" in said


def test_a_file_not_on_the_computer_is_said_so_and_nothing_is_asked(tmp_path):
    computer = Computer(tmp_path, {})
    with pytest.raises(cheese.PlatformToolError, match="没有这份文件"):
        cheese.run_platform_tool(
            "cheese_library",
            {"action": "give", "path": "nope.png"},
            computer,
            delegated=True,
        )
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
        "cheese_library",
        {"action": "take", "ref": "library/成绩.csv"},
        computer,
        delegated=True,
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
            "cheese_library",
            {"action": "take", "ref": "library/a.txt", "path": "a.txt"},
            computer,
            delegated=True,
        )
    assert not (tmp_path / "a.txt").exists()


@pytest.fixture
def platform_with_a_flaky_bucket():
    """The platform hands out the bucket address of one file; the bucket's
    first answer is cut off halfway, and it serves ranges after that."""
    content = bytes(range(256)) * 4000
    links: list[str] = []
    cut_off = [True]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/projects/p1/library/link"):
                links.append(self.path)
                body = json.dumps(
                    {
                        "data": {
                            "url": f"http://127.0.0.1:{self.server.server_port}/blob",
                            "path": "数据/表.csv",
                            "bytes": len(content),
                        }
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            start = 0
            wanted = self.headers.get("Range")
            if wanted:
                start = int(wanted.removeprefix("bytes=").split("-")[0])
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                )
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(content) - start))
            self.end_headers()
            if cut_off[0]:
                cut_off[0] = False
                self.wfile.write(content[start : len(content) // 2])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.wfile.write(content[start:])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", content, links
    server.shutdown()


def test_library_get_carries_on_a_cut_off_fetch_and_lands_the_whole_file(
    tmp_path, monkeypatch, platform_with_a_flaky_bucket
):
    """`cheese library get` fetches straight from the address it is handed; a
    fetch cut off halfway carries on from where it stopped, and only the whole
    file lands at the destination."""
    origin, content, _ = platform_with_a_flaky_bucket
    monkeypatch.setattr(cheese, "API", origin)
    out = tmp_path / "表.csv"

    cheese._library_get("p1", "数据/表.csv", str(out))

    assert out.read_bytes() == content
    assert not (tmp_path / "表.csv.part").exists()
