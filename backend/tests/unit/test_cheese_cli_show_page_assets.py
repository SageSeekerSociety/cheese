"""`cheese show` of a web page brings the files the page loads from beside it.

The page and its stylesheet live on the machine that made them, and the preview
serves the page from the platform's copy of the room's files. A page shown
alone arrived unstyled: its `style.css` never left the machine.
"""

import base64
import importlib.util
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


def _show(monkeypatch, root: Path, path: str) -> dict:
    """Run `cheese show <path>` from the worktree root; return what it sent."""
    cli = _load()
    sent: list[dict] = []
    monkeypatch.chdir(root)
    monkeypatch.setenv("CHEESE_WORKTREE_ROOT", str(root))
    monkeypatch.setattr(cli, "TOPIC", "t-1")
    monkeypatch.setattr(cli, "_pulled_versions", lambda: {})
    monkeypatch.setattr(cli, "_remember_pulled", lambda *_a: None)
    monkeypatch.setattr(
        cli, "_call", lambda _m, _p, body=None, **_k: sent.append(body) or {}
    )
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "show", path])
    cli.main()
    (body,) = sent
    return body


def _write(root: Path, rel: str, data: str | bytes) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        target.write_bytes(data)
    else:
        target.write_text(data)


def test_a_page_brings_its_stylesheet_scripts_images_and_what_its_css_loads(
    monkeypatch, tmp_path
):
    _write(
        tmp_path,
        "site/index.html",
        """<!doctype html><html><head>
        <link rel="stylesheet" href="style.css?v=2">
        <script src="./js/app.js" defer></script>
        <style>.hero{background:url('img/hero.png')}</style>
        </head><body>
        <img src="/img/logo.svg" srcset="img/logo@2x.png 2x">
        <a href="about.html">about</a>
        <img src="https://cdn.example.test/x.png">
        <img src="data:image/png;base64,AAAA">
        <link rel="stylesheet" href="../shared.css">
        <script src=".secret/key.js"></script>
        <img src="missing.png">
        </body></html>""",
    )
    _write(
        tmp_path, "site/style.css", "@import 'base.css'; body{font:url(fonts/a.woff2)}"
    )
    _write(tmp_path, "site/base.css", "p{}")
    _write(tmp_path, "site/fonts/a.woff2", b"\x00font")
    _write(tmp_path, "site/js/app.js", "console.log(1)")
    _write(tmp_path, "site/img/hero.png", b"\x89PNG hero")
    _write(tmp_path, "site/img/logo.svg", "<svg/>")
    _write(tmp_path, "site/img/logo@2x.png", b"\x89PNG 2x")
    _write(tmp_path, "site/about.html", "<p>about</p>")
    _write(tmp_path, "site/.secret/key.js", "secret")
    _write(tmp_path, "shared.css", "outside")

    body = _show(monkeypatch, tmp_path, "site/index.html")

    assert body["path"] == "site/index.html"
    sent = {a["path"]: base64.b64decode(a["content_b64"]) for a in body["assets"]}
    assert sent == {
        "site/style.css": b"@import 'base.css'; body{font:url(fonts/a.woff2)}",
        "site/base.css": b"p{}",
        "site/fonts/a.woff2": b"\x00font",
        "site/js/app.js": b"console.log(1)",
        "site/img/hero.png": b"\x89PNG hero",
        "site/img/logo.svg": b"<svg/>",
        "site/img/logo@2x.png": b"\x89PNG 2x",
    }


def test_a_page_that_loads_nothing_local_is_sent_alone(monkeypatch, tmp_path):
    _write(tmp_path, "report.html", "<p>all inline</p>")

    body = _show(monkeypatch, tmp_path, "report.html")

    assert "assets" not in body


def test_a_file_that_is_not_a_page_brings_nothing(monkeypatch, tmp_path):
    _write(tmp_path, "chart.svg", '<svg><image href="pic.png"/></svg>')
    _write(tmp_path, "pic.png", b"\x89PNG")

    body = _show(monkeypatch, tmp_path, "chart.svg")

    assert "assets" not in body
