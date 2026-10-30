"""The one place a reader's text loses the platform's own repeated words.

Every input the harness journals carries the platform's instructions to the
model — the chat contract on every turn, a nudge when the room went quiet. They
are said to the model, not by it, and a reader who is shown them reads the same
paragraph once per turn. Both readers of session records share this function so
that the 现场 and a pulled transcript file agree on what was said: 现场 through
`hide_platform_reminders`, a transcript through the `lines` view of the
session-records route.

The journal keeps every line. This is the display only (`without_reminders`).

The same two readers show a record to whoever opened it, and a session's input
carries the private memory index of the person its turn was for. Only that
person sees it (`hidden_private_memory`); everyone else in the room reads a
placeholder in its place, in the projected text and in the raw record alike.
"""

from typing import Any

from app.domain.agent.harness.notices import without_reminders
from app.domain.memory.instructions import without_others_private


def hidden_private_memory(value: Any, reader: str | None) -> Any:
    """``value`` (a page of rows or records) with every private memory index
    that is not ``reader``'s replaced (`without_others_private`), in place.

    Walked with a stack rather than by recursion: a record is whatever the
    harness wrote and can be nested deeper than the interpreter recurses."""
    if isinstance(value, str):
        return without_others_private(value, reader)
    stack = [value]
    while stack:
        node = stack.pop()
        pairs = (
            node.items()
            if isinstance(node, dict)
            else enumerate(node)
            if isinstance(node, list)
            else ()
        )
        for key, item in list(pairs):
            if isinstance(item, str):
                if "<private-memory" in item:
                    node[key] = without_others_private(item, reader)
            elif isinstance(item, (dict, list)):
                stack.append(item)
    return value


def no_platform_reminders(parts: list[dict]) -> tuple[list[dict], bool]:
    """`parts` with reminders taken out of their text; whether anything changed.

    A text part left with nothing goes, so a notice-only input leaves no empty
    row behind. Parts of other kinds are returned as they came.
    """
    kept: list[dict] = []
    changed = False
    for part in parts:
        value = part.get("text") if part.get("kind") == "text" else None
        if isinstance(value, str):
            hidden = without_reminders(value)
            if hidden != value:
                changed = True
                part = {**part, "text": hidden}
            if not hidden:
                continue
        kept.append(part)
    return kept, changed


def text_of(parts: list[dict]) -> str:
    """What a row reads as once its parts are laid out one under another."""
    return "\n".join(part.get("text", "") for part in parts)
