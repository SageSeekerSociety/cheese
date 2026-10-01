"""The sentences the platform says in a room, as a key and its parameters.

A room is read by several people at once, each in the language they picked, and
the language switch has to re-render lines that are already on the screen. So
the backend cannot pick the language: it says **which** sentence and **with
what**, and the reader's screen renders it
(``frontend/src/lib/noticeText.ts``, catalog ``roomNotice``).

The finished Chinese sentence is still stored as ``content``. Everything that
is not a screen reads that: the agents reading their room, browser push and the
desktop app (the server does not know the recipient's language), and any row
written before a line had a key.

``say()`` returns a :class:`NoticeText`, which *is* that Chinese string — every
function between the call site and the database keeps taking ``str`` — and
also carries the key. Where a line is stored, the key is written next to it in
``meta["i18n"]``, one entry per field it renders (``content``, ``detail``,
``detail_label``, ``title``)::

    meta["i18n"] = {"content": {"key": "acceptReady",
                                "params": {"pr": 12, "reviewer": "ana"}}}

A parameter may itself be a :class:`NoticeText` — a word the sentence chooses,
such as whose memory changed — and is then stored as a nested key. A list
parameter is rendered one item per line (the rows of a detail).

Anything done to a :class:`NoticeText` as a string (``+``, an f-string,
``.strip()``) returns a plain ``str`` and loses the key, which is the honest
result: that text is no longer the catalog's sentence.

``notice_messages.json`` holds the Chinese templates. The frontend's
``zh-CN/roomNotice.json`` holds the same ones and a test keeps the two equal, so
what the room stores and what a screen renders cannot say different things.

A refusal is a sentence too. An error raised with ``say()`` as its message
(``raise ValidationError(say("inviteSelf"))``) goes out with its key beside the
Chinese ``message`` in the error body (``error.i18n``, see
``app/core/errors.py``), and the browser shows it in its reader's language.
Those sentences live in ``error_messages.json``, mirrored by the frontend
catalog ``apiError``: they are shown in a toast or a form, not in a room. The
two files share one key space, so a key names one sentence wherever it is
said — an error's sentence can also end up in a room line.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

#: Where the per-field keys live in a block's ``meta``.
I18N_META_KEY: Final = "i18n"


def _templates(name: str) -> dict[str, str]:
    return json.loads(Path(__file__).with_name(name).read_text(encoding="utf-8"))


#: key → Chinese template of a room line, from ``notice_messages.json``.
NOTICE_MESSAGES: Final[dict[str, str]] = _templates("notice_messages.json")
#: key → Chinese template of an error, from ``error_messages.json``.
ERROR_MESSAGES: Final[dict[str, str]] = _templates("error_messages.json")
if NOTICE_MESSAGES.keys() & ERROR_MESSAGES.keys():
    raise RuntimeError(
        "a key is both a room line and an error: "
        f"{sorted(NOTICE_MESSAGES.keys() & ERROR_MESSAGES.keys())}"
    )

#: key → Chinese template, both catalogs. Placeholders are ``{name}``, the
#: syntax both ``str.format`` and vue-i18n read. A mention is a parameter
#: (``<@handle>``), never literal text: vue-i18n reads a bare ``@`` as a linked
#: message.
MESSAGES: Final[dict[str, str]] = {**NOTICE_MESSAGES, **ERROR_MESSAGES}


class NoticeText(str):
    """A platform sentence: the Chinese text, plus the key that renders it."""

    key: str
    params: Mapping[str, object]

    def __new__(cls, key: str, params: Mapping[str, object]) -> NoticeText:
        template = MESSAGES[key]
        text = template.format(**{k: _text(v) for k, v in params.items()})
        self = super().__new__(cls, text)
        self.key = key
        self.params = dict(params)
        return self

    def descriptor(self) -> dict:
        """The key and parameters, as stored in ``meta`` and notification payloads."""
        return {
            "key": self.key,
            "params": {name: _stored(value) for name, value in self.params.items()},
        }

    def __reduce__(self):  # pickling (copy.deepcopy) keeps the key
        return (NoticeText, (self.key, self.params))


def _text(value: object) -> str:
    # A list parameter is one item per line: the rows of a detail.
    if isinstance(value, (list, tuple)):
        return "\n".join(_text(item) for item in value)
    return str(value)


def _stored(value: object) -> object:
    if isinstance(value, NoticeText):
        return value.descriptor()
    if isinstance(value, (list, tuple)):
        return [_stored(item) for item in value]
    # Stored in JSON: a number stays a number, anything else (an id, an enum)
    # as the text it renders to.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return str(value)


def exception_text(exc: BaseException) -> str:
    """What ``exc`` was raised with, keeping its key when it was a sentence.

    ``str(exc)`` hands back a plain ``str``: a sentence raised as an exception's
    message would reach the room without its key."""
    said = exc.args[0] if len(exc.args) == 1 else None
    return said if isinstance(said, NoticeText) else str(exc)


def say(key: str, **params: object) -> NoticeText:
    """The sentence ``key`` with ``params`` filled in."""
    return NoticeText(key, params)


#: The fields of a block's ``meta`` that hold platform sentences, besides
#: ``content`` itself.
LOCALIZED_FIELDS: Final = ("detail", "detail_label", "title")


def from_descriptor(descriptor: Mapping) -> NoticeText | None:
    """The sentence a stored key names, or None when it names no sentence."""
    try:
        return NoticeText(
            str(descriptor["key"]),
            {
                name: _loaded(value)
                for name, value in dict(descriptor.get("params") or {}).items()
            },
        )
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def _loaded(value: object) -> object:
    if isinstance(value, Mapping):
        loaded = from_descriptor(value)
        if loaded is None:
            raise ValueError("not a sentence")
        return loaded
    if isinstance(value, list):
        return [_loaded(item) for item in value]
    return value


def with_keys(meta: Mapping | None, *, content: str) -> dict | None:
    """``meta`` carrying the key of every platform sentence in the block.

    ``content`` and the ``LOCALIZED_FIELDS`` of ``meta`` are looked at. A
    :class:`NoticeText` records its key. Plain text keeps a key already there
    only if that key still renders to exactly this text — ``meta`` may have
    been copied through JSON on its way here, which keeps the key and turns
    the sentence into a ``str``. Otherwise the key is dropped: the line was
    restated in words the catalog does not have, and an old key left behind
    would render the old sentence over the new one.
    """
    fields: dict[str, object] = {"content": content}
    for name in LOCALIZED_FIELDS:
        if meta is not None and name in meta:
            fields[name] = meta[name]
    keys = dict((meta or {}).get(I18N_META_KEY) or {})
    for name, value in fields.items():
        if isinstance(value, NoticeText):
            keys[name] = value.descriptor()
        elif name in keys:
            kept = from_descriptor(keys[name])
            if kept is None or value is None or str(kept) != value:
                keys.pop(name)
    if not keys:
        if meta is None or I18N_META_KEY not in meta:
            return None if meta is None else dict(meta)
        out = dict(meta)
        out.pop(I18N_META_KEY)
        return out
    return {**(meta or {}), I18N_META_KEY: keys}


def notice_keys(**fields: str | None) -> dict:
    """``{"i18n": ...}`` for the fields of a notice's ``meta`` that are
    platform sentences — merged into the ``meta`` it describes."""
    keys = {
        name: value.descriptor()
        for name, value in fields.items()
        if isinstance(value, NoticeText)
    }
    return {I18N_META_KEY: keys} if keys else {}


def notice_message(meta: Mapping | None) -> dict:
    """``{"message": key}`` for a notification about a stored line, or ``{}``.

    A notification shows the room's line; this is that line's key, carried
    beside its Chinese ``content`` so the reader's screen can render it."""
    keys = (meta or {}).get(I18N_META_KEY) or {}
    return {"message": keys["content"]} if "content" in keys else {}
