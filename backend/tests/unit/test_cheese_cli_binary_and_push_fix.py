"""两个新的 CLI 能力：二进制下载，和明确要求的重推。

`cheese api` used to decode every response as text. That is right for JSON and
silently destructive for anything else: `decode(errors="replace")` maps each
byte above 0x7f to U+FFFD, so a PNG comes back with its own signature mangled
and nothing says so — the file simply does not open. Attachments were always
reachable over that channel; only the printing was lossy.

`cheese push-fix` is the intentional half of the pair whose accidental half —
a snapshot on every CI poll tick — was removed. See
`tests/unit/test_ci_poll_does_not_commit.py`.
"""

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4


def test_api_writes_the_response_verbatim_as_bytes(monkeypatch, tmp_path, capsys):
    cli = _load()
    monkeypatch.setattr(
        cli.urllib.request, "urlopen", lambda *_a, **_k: _Response(_PNG)
    )
    target = tmp_path / "out.png"

    cli._raw_request("GET", "/topics/x/attachments/raw", None, str(target))

    assert target.read_bytes() == _PNG, "字节必须逐字落盘，一个都不能换"
    assert "out.png" in capsys.readouterr().out


def test_api_without_an_output_file_still_prints_text(monkeypatch, capsys):
    cli = _load()
    body = json.dumps({"code": 200}).encode()
    monkeypatch.setattr(
        cli.urllib.request, "urlopen", lambda *_a, **_k: _Response(body)
    )

    cli._raw_request("GET", "/topics/x/blocks", None)

    assert '"code": 200' in capsys.readouterr().out


def test_api_output_flag_reaches_the_request(monkeypatch, tmp_path):
    cli = _load()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_raw_request",
        lambda m, p, d, o=None: captured.update(method=m, path=p, data=d, out=o),
    )
    target = str(tmp_path / "f.bin")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["cheese", "api", "GET", "/topics/x/attachments/raw", "-o", target],
    )
    cli.main()
    assert captured["out"] == target


def test_push_fix_reports_where_it_pushed(monkeypatch, capsys):
    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(cli, "_task_id", lambda _: "task-1")
    monkeypatch.setattr(cli, "_sync_task", lambda _: None)
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *_a, **_k: {
            "data": {
                "pushed": True,
                "pr_number": 615,
                "pr_url": "https://example.test/pull/615",
            }
        },
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "push-fix"])
    cli.main()
    out = capsys.readouterr().out
    assert "615" in out


def test_push_fix_says_why_when_there_was_nothing_to_push(monkeypatch, capsys):
    """Not an error. An agent that gets an exception for "already pushed"
    learns to stop calling this, which is the opposite of what it is for."""
    cli = _load()
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(cli, "_task_id", lambda _: "task-1")
    monkeypatch.setattr(cli, "_sync_task", lambda _: None)
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *_a, **_k: {
            "data": {"pushed": False, "reason": "这个话题手上没有正在等 CI 的 PR"}
        },
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "push-fix"])
    cli.main()
    assert "没有正在等 CI 的 PR" in capsys.readouterr().out
