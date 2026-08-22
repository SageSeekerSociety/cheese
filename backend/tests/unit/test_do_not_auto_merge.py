"""A deliberate failure, asked for by name — delete this file to merge.

This branch carries the #608 foundation, which was reviewed and approved once
and then merged as an empty PR: the work never left the machine it was written
on. It is being re-delivered here so CI can run on it. A green PR would merge
on its own before anyone had looked at the recovered diff, which is exactly what
must not happen twice, so this one assertion holds the gate shut.

It touches nothing else. Deleting this file is the whole of "make it mergeable".
"""

import pytest


def test_do_not_auto_merge() -> None:
    pytest.fail(
        "deliberate: delete backend/tests/unit/test_do_not_auto_merge.py to merge"
    )
