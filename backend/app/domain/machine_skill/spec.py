"""The agentskills.io rules a skill must pass before the platform lands one.

https://agentskills.io/specification makes four things mandatory and nothing
else: ``name`` equals the skill's own directory name, ``name`` reads as
``^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$`` and is at most 64 characters, and
``description`` is non-empty and at most 1024.

The machine re-checks these on the far side (:mod:`machine_skill.scan`, a
stdlib program that cannot import this) — the same rules live twice on purpose.
This copy is what the platform answers a *fetched* skill with, before anything
is written onto somebody's machine: a repo that is not a skill is refused here,
not discovered later as a directory the scan quietly passes over.
"""

from __future__ import annotations

import re

#: Uppercase is let in: Trae ships its own skills as ``TRAE-xxx``, and the spec
#: only mandates lowercase — refusing it would drop a whole platform's set of
#: working skills (``machine_skill/scan.py`` takes the same stand).
NAME_RE = re.compile(r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024


def check(name: str, description: str) -> str | None:
    """``None`` when the skill passes, else the ``say()`` key to refuse it with.

    「name equals the directory name」 is the caller's business: a landed skill
    is written under its own name, so that one holds by construction.
    """
    if not name or len(name) > MAX_NAME or not NAME_RE.match(name):
        return "skillWebSpec"
    if not description.strip() or len(description) > MAX_DESCRIPTION:
        return "skillWebSpec"
    return None
