"""A source file shown in the room is text, not a web page.

`cheese show app.py` used to fall through to `html`: the content domain sent a
`.py` as `text/html` and the panel tried to run it as a page. Code and plain
text now have their own kind, `text`, sent as `text/plain`; the panel draws
them with line numbers from the extension.

The CLI ships into the sandbox as one stdlib-only file, so it carries its own
copy of the suffix table. A suffix that only one side knows is either rejected
on arrival or shown as the wrong kind, so the two copies must agree."""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.project.room_files import (
    _ARTIFACT_KIND_BY_SUFFIX,
    ARTIFACT_MIME,
    artifact_kind_for,
)

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _cli():
    loader = SourceFileLoader("cheese_cli_shown_code", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path",
    ["app.py", "src/main.ts", "Widget.tsx", "Main.java", "lib.rs", "run.sh", "a.json", "notes.txt"],
)
def test_code_and_plain_text_are_shown_as_text(path):
    assert artifact_kind_for(path) == "text"
    assert ARTIFACT_MIME["text"] == "text/plain"


def test_web_pages_and_unknown_suffixes_still_default_to_html():
    assert artifact_kind_for("index.html") == "html"
    assert artifact_kind_for("drawing.svg") == "svg"
    assert artifact_kind_for("Makefile") == "html"


def test_the_cli_and_the_backend_agree_on_every_suffix():
    cli = _cli()
    assert cli.ARTIFACT_KIND_BY_SUFFIX == _ARTIFACT_KIND_BY_SUFFIX
    assert cli.artifact_kind_for("app.py") == "text"
    # Text kinds travel as text: the CLI decodes them, the backend stores the bytes.
    assert "text" not in cli.BINARY_ARTIFACT_KINDS
