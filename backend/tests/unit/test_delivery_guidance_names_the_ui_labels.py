"""The teammate tells a person where to click with the words the screen shows.

A delivery waiting on a person is accepted, sent back, or voided by closing the
task, and only a person's click on the interface does any of that. When the
teammate is refused a close, or a person types 「采纳」 or 「关掉吧」 in the
conversation, the teammate answers with where to click. What it says comes from
two backend texts, the refusal (`taskCloseWhileDeliveryWaits`) and the
`cheese_task` / `cheese_card` tool descriptions, while the buttons it names are
drawn from the frontend's catalog. A label renamed on the screen and not here
would send the person looking for a button that does not exist, so each label
it names is checked against the catalog the screen renders from.
"""

import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

from app.core.sentences import TEMPLATES

ROOT = Path(__file__).resolve().parents[3]
MESSAGES = ROOT / "frontend/src/i18n/messages"
CLI = ROOT / "backend/sandbox/cheese"

#: (catalog file, key): the buttons and places a person uses to decide a
#: waiting delivery — the bar above the input box (accept for a last step and
#: for an earlier one, send back, and the review button a phone shows instead),
#: the Changes tab, and the task page's More actions menu with Close task.
LABELS = (
    ("work/room/accept/index.json", "acceptAndComplete"),
    ("work/room/accept/index.json", "acceptAction"),
    ("work/room/accept/index.json", "review"),
    ("work/room/accept/index.json", "sendBack"),
    ("work/room/tabs/index.json", "changes"),
    ("work/task/index.json", "more"),
    ("work/task/index.json", "close"),
)


def _label(locale: str, catalog: str, key: str) -> str:
    return json.loads((MESSAGES / locale / catalog).read_text(encoding="utf-8"))[key]


def _cli():
    loader = SourceFileLoader("cheese_cli_for_labels", str(CLI))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize(("locale", "quote"), [("zh-CN", "「{}」"), ("en", "“{}”")])
def test_the_refusal_names_the_buttons_as_the_screen_shows_them(locale, quote):
    refusal = TEMPLATES[locale]["taskCloseWhileDeliveryWaits"]

    for catalog, key in LABELS:
        assert quote.format(_label(locale, catalog, key)) in refusal, key


def test_the_tool_descriptions_name_the_buttons_as_the_screen_shows_them():
    cli = _cli()
    guidance = cli.DELIVERY_IS_DECIDED_IN_THE_UI
    verbs = cli.PLATFORM_TOOLS._actions

    for catalog, key in LABELS:
        assert f"「{_label('zh-CN', catalog, key)}」" in guidance, key
    # Both verbs a teammate reaches for at that moment carry it.
    assert guidance in verbs["cheese_task"]["close"]["description"]
    assert guidance in verbs["cheese_card"]["request"]["description"]
