"""A process and its descendants on the executor, which a stop reaches whole.
Shipped beside `runtime.py` (`RELEASE_FILES`), standard library only, and run
by Pythons as old as 3.9; not used on Windows (`portable.terminate_tree`).
"""

from __future__ import annotations

import runpy
from pathlib import Path


def tree(pid):
    """The process and its descendants, parents first. A descendant that
    left the group (`setsid`) is still one: the build's own stop kills the
    tree, not the group."""
    rows = []
    if Path("/proc").is_dir():
        # /proc rather than `ps`: the private execution image has no `ps`.
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            # The command name sits in parentheses and may hold spaces;
            # the parent pid is the second field after it.
            rows.append((int(entry.name), int(stat[stat.rindex(")") + 2 :].split()[1])))
    else:
        # macOS, where not even `ps` runs in a room's sandbox.
        rows = runpy.run_path(str(Path(__file__).with_name("darwin.py")))["processes"]()
    children = {}
    for child, parent in rows:
        children.setdefault(parent, []).append(child)
    found, pending = [], [pid]
    while pending:
        current = pending.pop(0)
        if current not in found:
            found.append(current)
            pending.extend(children.get(current, []))
    return found
