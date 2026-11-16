"""The prose pieces under `pieces/` and the three markers written in them.

A piece is a markdown file holding text and nothing else: no conditions, no
loops. Three markers mark what is not prose:

- ``⟦slot:name⟧`` — a value filled in when the piece is rendered;
- ``⟦tool:cheese_task.open⟧`` / ``⟦tool:chat_send⟧`` — a platform tool, rendered
  as `` `cheese_task(action=open)` `` / `` `chat_send` ``. A profile that renders
  a piece naming a tool it does not hold fails (`render.ProfileError`), so a
  rule can never point a session at a tool it lacks;
- ``⟦piece:id⟧`` — another piece, included whole.

Not ``{}``: the text itself contains braces (``${T:?T is not set}``). Markers are substituted in one pass over the template,
so a slot's value — a person's words — is never read for markers.

A file ends with one newline that is not part of the text (editors and diffs
want it); everything else is the text byte for byte.
"""

import functools
import re
from pathlib import Path

PIECES = Path(__file__).parent / "pieces"

_MARK = re.compile(r"⟦(slot|tool|piece):([^⟧]+)⟧")


class PieceError(KeyError):
    """A piece that does not exist, or a slot left unfilled."""


@functools.cache
def raw(piece_id: str) -> str:
    """The piece's text with its includes expanded and its other markers left in."""
    path = PIECES / f"{piece_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as missing:
        raise PieceError(
            f"no piece {piece_id}"
        ) from missing  # i18n-exempt: a missing prompt file, for a developer
    text = text.removesuffix("\n")
    return _MARK.sub(
        lambda m: raw(m.group(2)) if m.group(1) == "piece" else m.group(0), text
    )


def includes(piece_id: str) -> tuple[str, ...]:
    """The piece and every piece it includes, depth first, in reading order."""
    path = PIECES / f"{piece_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as missing:
        raise PieceError(
            f"no piece {piece_id}"
        ) from missing  # i18n-exempt: a missing prompt file, for a developer
    found = [piece_id]
    for m in _MARK.finditer(text):
        if m.group(1) == "piece":
            found.extend(includes(m.group(2)))
    return tuple(found)


def tools_in(piece_id: str) -> frozenset[str]:
    """The tools the piece names (`cheese_task.open`, `chat_send`), includes too."""
    return frozenset(
        m.group(2) for m in _MARK.finditer(raw(piece_id)) if m.group(1) == "tool"
    )


def tool_text(key: str) -> str:
    """How a tool marker reads: `` `x(action=y)` `` for a verb, `` `x` `` for a tool."""
    name, _, verb = key.partition(".")
    return f"`{name}(action={verb})`" if verb else f"`{name}`"


def piece(piece_id: str, **slots: object) -> str:
    """The piece rendered: tools written out, slots filled from ``slots``."""

    def one(m: re.Match[str]) -> str:
        kind, name = m.group(1), m.group(2)
        if kind == "tool":
            return tool_text(name)
        if name not in slots:
            raise PieceError(
                f"{piece_id}: slot {name} not given"
            )  # i18n-exempt: a profile left a slot unfilled, for a developer
        return str(slots[name])

    return _MARK.sub(one, raw(piece_id))


def in_language(piece_id: str, language: str | None, **slots: object) -> str:
    """A piece written in two languages (`x.zh.md`, `x.en.md`), in the reader's.

    The platform's words go in the language of the person who will read them
    (their interface setting); unset, they are the stored Chinese, like every
    other sentence the platform keeps. Only ``en`` reads English."""
    return piece(f"{piece_id}.{'en' if language == 'en' else 'zh'}", **slots)
