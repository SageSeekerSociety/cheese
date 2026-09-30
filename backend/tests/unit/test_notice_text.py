"""The platform's room sentences: one catalog, two ends, and the key survives.

The backend stores a room line as Chinese text plus the key and parameters of
the sentence (`app/domain/block/notice_text.py`); a screen renders the key in its
reader's language from the frontend catalog `roomNotice` (an error's sentence
from `apiError`). Two things can go
wrong without anything failing at the moment they happen:

- the two copies of the Chinese templates drift, and the room stores one
  sentence while a Chinese screen renders another;
- a call site names a key the catalog does not have, or fills it with the wrong
  parameters, which raises only when that notice is finally said in production.

Both ends are read from their real files here, and every ``say(...)`` in the
backend is checked against the catalog, so either mistake fails at the commit
that makes it.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from app.domain.block.notice_text import (
    ERROR_MESSAGES,
    I18N_META_KEY,
    MESSAGES,
    NOTICE_MESSAGES,
    notice_message,
    say,
    with_keys,
)

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "backend/app"
CATALOG = ROOT / "frontend/src/i18n/messages"


#: Each backend catalog and the frontend namespace that mirrors it.
CATALOGS = [("roomNotice", NOTICE_MESSAGES), ("apiError", ERROR_MESSAGES)]


def _catalog(locale: str, namespace: str) -> dict[str, str]:
    return json.loads((CATALOG / locale / f"{namespace}.json").read_text("utf-8"))


def _placeholders(template: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", template))


@pytest.mark.parametrize(("namespace", "templates"), CATALOGS)
def test_the_backend_templates_are_the_frontend_chinese_catalog(namespace, templates):
    assert _catalog("zh-CN", namespace) == templates


@pytest.mark.parametrize(("namespace", "templates"), CATALOGS)
def test_every_sentence_has_english_with_the_same_placeholders(namespace, templates):
    english = _catalog("en", namespace)
    assert set(english) == set(templates)
    assert {
        key: (_placeholders(templates[key]), _placeholders(english[key]))
        for key in templates
        if _placeholders(templates[key]) != _placeholders(english[key])
    } == {}


def _say_calls() -> list[tuple[str, int, str | None, set[str] | None]]:
    """Every ``say(...)`` in the app: (file, line, key, keyword names).

    The key is None when it is not a string literal, and the names None when
    the call spreads a mapping."""
    calls = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "say"
            ):
                continue
            first = node.args[0] if node.args else None
            key = (
                first.value
                if isinstance(first, ast.Constant) and isinstance(first.value, str)
                else None
            )
            names = (
                None
                if any(k.arg is None for k in node.keywords)
                else {k.arg for k in node.keywords if k.arg}
            )
            calls.append((str(path.relative_to(ROOT)), node.lineno, key, names))
    return calls


def test_every_say_names_a_sentence_and_fills_exactly_its_placeholders():
    calls = _say_calls()
    assert calls, "no say() call found — is the scan looking in the right place?"
    # A key chosen from a table (`say(_LABEL[kind])`) is checked where it is
    # named, by the test below; its parameters only when the call is made.
    wrong = [
        f"{file}:{line} say({key!r}) with {sorted(names or ())}"
        for file, line, key, names in calls
        if key is not None
        and (
            key not in MESSAGES
            or (names is not None and names != _placeholders(MESSAGES[key]))
        )
    ]
    assert wrong == []


def test_every_sentence_in_the_catalog_is_said_somewhere():
    """A key some table hands to ``say`` counts: it is named in the app as a
    string literal all the same. One named nowhere is a sentence nobody says."""
    named = {
        node.value
        for path in APP.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text("utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert sorted(set(MESSAGES) - named) == []


def test_a_sentence_is_its_chinese_text_and_carries_its_key():
    line = say("acceptReady", pr=12, reviewer="ana")
    assert line == "PR #12 可以合并了，等 ana 采纳"
    meta = with_keys({"event_type": "accept_ready"}, content=line)
    assert meta is not None
    assert meta[I18N_META_KEY]["content"] == {
        "key": "acceptReady",
        "params": {"pr": 12, "reviewer": "ana"},
    }
    assert notice_message(meta) == {"message": meta[I18N_META_KEY]["content"]}


def test_nested_and_list_parameters_are_stored_as_keys():
    detail = say(
        "lines",
        items=[
            say("artifactVersion", name="报告", version=2),
            say("artifactUndelivered", name="数据"),
        ],
    )
    assert detail == "《报告》 第 2 版\n《数据》 尚未交付"
    meta = with_keys({"detail": detail}, content="x")
    assert meta is not None
    assert meta[I18N_META_KEY]["detail"] == {
        "key": "lines",
        "params": {
            "items": [
                {"key": "artifactVersion", "params": {"name": "报告", "version": 2}},
                {"key": "artifactUndelivered", "params": {"name": "数据"}},
            ]
        },
    }


def test_a_key_survives_meta_copied_through_json():
    """`meta` can be serialised on its way to the row (a retry queue, a copy):
    the sentence becomes a plain ``str`` but its key is already in ``i18n``."""
    line = say("acceptReady", pr=3, reviewer="ana")
    meta = with_keys({"detail_label": say("labelNextStep")}, content=line)
    assert meta is not None
    copied = json.loads(json.dumps(meta))
    again = with_keys(copied, content=str(line))
    assert again is not None
    assert again[I18N_META_KEY] == meta[I18N_META_KEY]


@pytest.mark.parametrize("field", ["content", "detail_label"])
def test_a_line_restated_in_other_words_drops_its_old_key(field):
    """An in-place update to text the catalog does not have must not leave the
    old key behind — a screen would render the old sentence over the new."""
    meta = with_keys(
        {"detail_label": say("labelNextStep")},
        content=say("acceptReady", pr=3, reviewer="ana"),
    )
    assert meta is not None
    if field == "content":
        restated = with_keys(meta, content="别的话")
    else:
        restated = with_keys({**meta, "detail_label": "别的话"}, content="x")
    assert restated is not None
    assert field not in restated.get(I18N_META_KEY, {})
