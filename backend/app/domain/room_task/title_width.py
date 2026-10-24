"""How wide a task's title may be.

A task's title is drawn on one line in the sidebar and whatever does not fit is
cut with an ellipsis (``frontend/src/components/topic-sidebar/TopicRailTaskRow.vue``),
so a title that does not fit is a title nobody can read. That row is
``padding-inline-start: 40px + depth * 20px`` inside a sidebar that defaults to
280px (``frontend/src/composables/useSidebarWidth.ts``) at a 14px font: about 14
汉字 for a task at the top level, 12 once it is nested under its channel — which
is how most tasks are drawn. The limit below is the nested one, so a title that
fits is drawn whole in both places.

What is counted is width, not characters: a 汉字 is about twice as wide as a
Latin letter or a digit, and the sidebar runs out of pixels, not of code points.
Counting code points instead would cut an English title at half the visual
length — and the reason for the limit at all is that a person has to read the
title in that one line.

The places that tell an AI teammate how long a title may be all read the numbers
below: ``naming_model.py`` (the platform naming a task), ``agent/harness/prompt.py``
(the reminder to name an unnamed task) and ``cheese_task(action=open)`` / ``cheese_room(action=rename)`` in
``backend/sandbox/cheese``, which cannot import this module and spells the
number out. ``topics_tasks.py`` and ``topics_title.py`` hold the API to it;
``naming_model.py`` asks the model again when its title is too wide and preserves
the old name if no usable rewrite comes back.
"""

import unicodedata

#: How wide a title may be, in half-characters. A 汉字 is 2.
TITLE_WIDTH = 24
#: The same limit as the count a person reads: 12 汉字 wide.
TITLE_CHARS = TITLE_WIDTH // 2

#: East Asian Width classes that are drawn double: Wide and Fullwidth. Anything
#: else — Latin letters, digits, spaces — is one half-character. Ambiguous
#: characters (``·``, ``°``) are drawn narrow in the fonts the sidebar uses.
_DOUBLE = frozenset("WF")


def title_width(title: str) -> int:
    """How many half-characters `title` takes up in the sidebar."""
    return sum(
        2 if unicodedata.east_asian_width(char) in _DOUBLE else 1 for char in title
    )


def title_fits(title: str) -> bool:
    """Whether `title` is narrow enough to be drawn whole."""
    return title_width(title) <= TITLE_WIDTH
