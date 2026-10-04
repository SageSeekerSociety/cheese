"""`cheese check`: what the platform's browser saw, said so an agent acts on it."""

from __future__ import annotations

import base64
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
_PNG = base64.b64encode(b"\x89PNG fake").decode()


def _load():
    loader = SourceFileLoader("cheese_cli", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _view(width: int, **extra) -> dict:
    return {
        "width": width,
        "viewport_width": width,
        "page_width": width,
        "page_height": 2000,
        "overflowing": [],
        "broken_images": [],
        "console_errors": [],
        "failed_requests": [],
        "cut_at": None,
        "png_b64": _PNG,
        **extra,
    }


def _run(monkeypatch, tmp_path, answer, *argv):
    cli = _load()
    page = tmp_path / "out.html"
    page.write_text("<main>hi</main>", encoding="utf-8")
    sent: list[dict] = []

    def _call(method, path, body=None, **kw):
        sent.append({"method": method, "path": path, "body": body, **kw})
        return answer

    monkeypatch.setattr(cli, "_call", _call)
    monkeypatch.setattr(cli, "CHECK_DIR", str(tmp_path / "shots"))
    monkeypatch.setattr(cli.sys, "argv", ["cheese", "check", str(page), *argv])
    cli.main()
    return sent


def test_a_page_that_fits_says_so_and_leaves_both_screenshots(
    monkeypatch, tmp_path, capsys
):
    answer = {"data": {"ok": True, "views": [_view(400), _view(1280)]}}
    sent = _run(monkeypatch, tmp_path, answer)

    assert sent[0]["path"] == "/page-check"
    assert sent[0]["body"]["widths"] == [400, 1280]
    assert sent[0]["body"]["html"] == "<main>hi</main>"
    out = capsys.readouterr().out
    assert "没有发现问题" in out
    for width in (400, 1280):
        assert (tmp_path / "shots" / f"out-{width}.png").read_bytes() == b"\x89PNG fake"


def test_what_pushes_the_page_wider_and_what_failed_is_named(
    monkeypatch, tmp_path, capsys
):
    narrow = _view(
        400,
        page_width=908,
        overflowing=[{"element": "div.kpi-row", "right": 908, "width": 900}],
        broken_images=["chart.png"],
        console_errors=["nope is not defined"],
    )
    wide = _view(1280, console_errors=["nope is not defined"])
    answer = {"data": {"ok": True, "views": [narrow, wide]}}
    _run(monkeypatch, tmp_path, answer, "--width", "400", "--width", "1280")

    out = capsys.readouterr().out
    assert "撑宽到 908px" in out and "div.kpi-row" in out
    assert "chart.png" in out
    # The same error at two widths is one thing to fix, not two.
    assert out.count("nope is not defined") == 1
    assert "改一轮" in out


def test_a_platform_without_a_renderer_says_so_and_fails(monkeypatch, tmp_path, capsys):
    answer = {"ok": False, "status": 503, "error": {"message": "No page renderer"}}
    with pytest.raises(SystemExit) as exited:
        _run(monkeypatch, tmp_path, answer)
    assert exited.value.code == 1
    assert "No page renderer" in capsys.readouterr().err
