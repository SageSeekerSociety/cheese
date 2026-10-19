"""A path the backend recorded, expanded the way the connector expands it.

The backend names machine paths with a literal ``$HOME`` and the enrollment's
root as ``${CHEESE_PROFILE_ROOT:-.cheese}``; the connector resolves both
(`cli/internal/host`). A fake machine that expands only ``$HOME`` puts the
session somewhere no real machine would.
"""

from pathlib import Path

PROFILE_ROOT = "${CHEESE_PROFILE_ROOT:-.cheese}"


def on_machine(recorded: str, home: Path | str, root: str = ".cheese") -> str:
    return recorded.replace(PROFILE_ROOT, root).replace("$HOME", str(home))


def resolved(recorded: str, root: str = ".cheese") -> str:
    """``recorded`` with the enrollment's root named, ``$HOME`` left as is: two
    spellings the connector reads as one directory compare equal."""
    return recorded.replace(PROFILE_ROOT, root)
