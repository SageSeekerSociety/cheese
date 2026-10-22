"""An answer that has started saying the same thing over and over.

A person's 芝士 answers on whatever model their gateway serves, and a weaker
one can fall into writing one passage again and again until its output limit.
That is caught as it is written: the end of the text is the same passage, of at
least ``SHORTEST`` characters, ``TIMES`` times in a row. Shorter repeats are
ordinary writing (a list of 「无」, a table's rules); a passage that long said
three times running is not.
"""

from __future__ import annotations

SHORTEST = 60
LONGEST = 600
TIMES = 3
#: How much more text is written before the end is looked at again.
LOOK_EVERY = 200


def repeating(text: str) -> bool:
    tail = text[-LONGEST * TIMES :]
    for size in range(SHORTEST, LONGEST + 1):
        if len(tail) < size * TIMES:
            return False
        unit = tail[-size:]
        if unit.strip() and tail[-size * TIMES :] == unit * TIMES:
            return True
    return False
