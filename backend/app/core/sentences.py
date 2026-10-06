"""The sentences the platform says — room lines, refusals, pushes — as a key
and its parameters.

Infrastructure, not a domain: every layer says them, ``app/core/errors.py``
included, and the module needs nothing but the standard library and the
catalog files.

A room is read by several people at once, each in the language they picked, and
the language switch has to re-render lines that are already on the screen. So
the backend cannot pick the language: it says **which** sentence and **with
what**, and the reader's screen renders it
(``frontend/src/lib/noticeText.ts``, catalog ``roomNotice``).

The finished Chinese sentence is still stored as ``content``. The agents
reading their room read that, and so does anything that meets a row written
before a line had a key.

What reaches one person away from a screen — browser push, the desktop app's
system notifications — is rendered here instead, in that person's own language
(``user.language``, see :func:`render`): it is one recipient, and nothing has
to change once it is shown. Web Push has nothing like APNs ``loc-key``: the
payload is encrypted for the browser and shown as it arrives, so the words are
chosen on the server. The few words a push puts around the line (``pushInRoom`` and
the like, ``notification/push.py``) are sentences of ``roomNotice`` too.

``say()`` returns a :class:`NoticeText`, which *is* that Chinese string — every
function between the call site and the database keeps taking ``str`` — and
also carries the key. Where a line is stored, the key is written next to it in
``meta["i18n"]``, one entry per field it renders (``content``, ``detail``,
``detail_label``, ``title``)::

    meta["i18n"] = {"content": {"key": "acceptReady",
                                "params": {"pr": 12, "reviewer": "ana"}}}

A parameter may itself be a :class:`NoticeText` — a word the sentence chooses,
such as whose memory changed — and is then stored as a nested key. A list
parameter is rendered one item per line (the rows of a detail). A list said
inside a sentence — the rooms that collided, the names that were not woken —
is a :func:`listing`: its items go out as they are, and each screen joins and
quotes them the way its reader's language does.

Anything done to a :class:`NoticeText` as a string (``+``, an f-string,
``.strip()``) returns a plain ``str`` and loses the key, which is the honest
result: that text is no longer the catalog's sentence.

The templates are the frontend's catalogs, read from where they are written
(``frontend/src/i18n/messages/<locale>/roomNotice.json``): there is one copy, so
what the room stores, what a screen renders and what a push says cannot say
different things. The backend image carries those files at the same place
relative to this module (``backend/Dockerfile``, the ``i18n`` build context).

A refusal is a sentence too. An error raised with ``say()`` as its message
(``raise ValidationError(say("inviteSelf"))``) goes out with its key beside the
Chinese ``message`` in the error body (``error.i18n``, see
``app/core/errors.py``), and the browser shows it in its reader's language.
Those sentences live in the catalog ``apiError``: they are shown in a toast or
a form, not in a room. The two catalogs share one key space, so a key names one
sentence wherever it is said — an error's sentence can also end up in a room
line.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

#: Where the per-field keys live in a block's ``meta``.
I18N_META_KEY: Final = "i18n"


#: The frontend's catalogs. Three levels above ``app/core/`` is the repository
#: root in a checkout (``backend/app/core``), and the filesystem root in the
#: backend image (``/app/app/core``), where the Dockerfile puts the same files
#: under the same relative path.
CATALOG_DIR: Final = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "i18n" / "messages"
)

#: The UI languages a person can pick (``frontend/src/i18n/index.ts``). The
#: stored sentence is in the first.
LOCALES: Final = ("zh-CN", "en")


def _templates(locale: str, namespace: str) -> dict[str, str]:
    path = CATALOG_DIR / locale / f"{namespace}.json"
    return json.loads(path.read_text(encoding="utf-8"))


#: key → Chinese template of a room line, from ``roomNotice``.
NOTICE_MESSAGES: Final[dict[str, str]] = _templates("zh-CN", "roomNotice")
#: key → Chinese template of an error, from ``apiError``.
ERROR_MESSAGES: Final[dict[str, str]] = _templates("zh-CN", "apiError")
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

#: locale → how one item of a quoted listing is written (``global.listItemQuoted``,
#: the template the frontend quotes with).
QUOTED_ITEM: Final[dict[str, str]] = {
    locale: _templates(locale, "global")["listItemQuoted"] for locale in LOCALES
}

#: locale → key → template, both catalogs.
TEMPLATES: Final[dict[str, dict[str, str]]] = {
    "zh-CN": MESSAGES,
    **{
        locale: {
            **_templates(locale, "roomNotice"),
            **_templates(locale, "apiError"),
        }
        for locale in LOCALES[1:]
    },
}

#: Keys that only replay stored room/notification descriptors; new notices must
#: not say them. Ordinary comments no longer schedule agent turns, and agents no
#: longer record decisions or add milestones (the 「记录了决策」 and 「添加了里程碑」
#: lines stay on old rooms). A task is created by a person and worked in its
#: own conversation rather than dispatched to a room's subagent (「派出一条活」,
#: the room's 「已转为任务」 line and the subagent's start and empty stop stay on
#: old rooms). A comment naming the agent is answered in its thread, not by a
#: turn of the room (「在文档评论里提到了」 stays on old rooms). A private chat's
#: message no longer becomes a channel (「一条消息已转为频道」 stays on old ones).
#: The platform renames tasks quietly, without a line (「标题自动更新为」 stays on
#: old rooms).
HISTORICAL_NOTICE_KEYS: Final = frozenset(
    {
        "docCommented",
        "docCommentedHandedTo",
        "docCommentMentioned",
        "actionDecision",
        "actionMilestone",
        "taskDispatched",
        "blockUpgradedToTask",
        "blockUpgradedTaskId",
        "blockUpgradedToRoom",
        "blockUpgradedRoomId",
        "labelUpgradedTo",
        "subagentStart",
        "subagentStopEmpty",
        "titleAutoRenamed",
    }
)


class NoticeList:
    """Several items said as one parameter of a sentence (:func:`listing`).

    Its Chinese text is the items joined with 「、」, each in 「」 when
    ``quoted``: the words the sentence always had. Stored, it is
    ``{"list": [...], "quoted": bool}``, and the reader's screen joins the items
    with its own language's list pattern and quotes."""

    def __init__(self, items: Iterable[object], quoted: bool = False) -> None:
        self.items = list(items)
        self.quoted = quoted

    def __str__(self) -> str:
        words = (_text(item) for item in self.items)
        return "、".join(f"「{w}」" if self.quoted else w for w in words)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, NoticeList)
            and other.items == self.items
            and other.quoted == self.quoted
        )

    def __hash__(self) -> int:
        return hash((tuple(map(str, self.items)), self.quoted))


def listing(items: Iterable[object], *, quoted: bool = False) -> NoticeList:
    """``items`` as one parameter of a sentence; ``quoted`` puts each in quotes
    (room titles, file names)."""
    return NoticeList(items, quoted)


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
    if isinstance(value, NoticeList):
        return str(value)
    # A list parameter is one item per line: the rows of a detail.
    if isinstance(value, (list, tuple)):
        return "\n".join(_text(item) for item in value)
    return str(value)


def _stored(value: object) -> object:
    if isinstance(value, NoticeText):
        return value.descriptor()
    if isinstance(value, NoticeList):
        return {"list": [_stored(item) for item in value.items], "quoted": value.quoted}
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


def error_frame(message: str, **fields: object) -> dict:
    """A live ``error`` frame: a room socket's, or a stream's ``event: error``.

    ``message`` goes out as it is, for agents and anything else that is not a
    screen. A sentence also carries its key as ``i18n``, the shape an error
    body's ``error.i18n`` has, so the screen renders it in its reader's
    language the same way."""
    frame = {**fields, "message": message}
    if isinstance(message, NoticeText):
        frame["i18n"] = message.descriptor()
    return frame


def say(key: str, **params: object) -> NoticeText:
    """A current sentence with ``params`` filled in; replay uses from_descriptor."""
    if key in HISTORICAL_NOTICE_KEYS:
        raise ValueError(f"historical notice cannot be generated: {key}")
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
    if isinstance(value, Mapping) and isinstance(value.get("list"), list):
        return NoticeList(
            [_loaded(item) for item in value["list"]], bool(value.get("quoted"))
        )
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


def render(descriptor: object, locale: str | None) -> str | None:
    """The sentence a stored key names, said in ``locale``.

    None when it cannot be said there — an unknown locale (a person who never
    picked one), a key or a nested key that language does not have, parameters
    that do not fit — and the caller shows the stored Chinese text, as a screen
    does. A nested sentence is said in the same language, never half in one and
    half in the other."""
    templates = TEMPLATES.get(locale or "")
    if locale is None or templates is None or not isinstance(descriptor, Mapping):
        return None
    try:
        return _render(descriptor, templates, locale)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def _render(descriptor: Mapping, templates: Mapping[str, str], locale: str) -> str:
    params = dict(descriptor.get("params") or {})
    return templates[str(descriptor["key"])].format(
        **{name: _rendered(value, templates, locale) for name, value in params.items()}
    )


def _rendered(value: object, templates: Mapping[str, str], locale: str) -> object:
    if isinstance(value, Mapping) and isinstance(value.get("list"), list):
        items = [str(_rendered(item, templates, locale)) for item in value["list"]]
        if value.get("quoted"):
            items = [QUOTED_ITEM[locale].format(item=item) for item in items]
        return _joined(items, locale)
    if isinstance(value, Mapping):
        return _render(value, templates, locale)
    if isinstance(value, list):
        return "\n".join(str(_rendered(item, templates, locale)) for item in value)
    return value


def _joined(items: list[str], locale: str) -> str:
    """``items`` as one phrase, the way a screen joins them
    (``Intl.ListFormat``: Chinese narrow, English long)."""
    if locale != "en":
        return "、".join(items)
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def in_language(sentence: NoticeText, locale: str | None) -> str:
    """``sentence`` in ``locale``, or in Chinese when that language lacks it."""
    return render(sentence.descriptor(), locale) or str(sentence)
