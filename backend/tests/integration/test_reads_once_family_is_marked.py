"""The "reads once" family is one marker, and it is derived, not remembered.

Eighteen tests already asserted on how many SQL statements a request issues.
They used twelve different names for it, and the name only ever appeared in the
file name: searching the tree for the thing they had in common found six of
them. `pytest -m reads_once` is the answer now, and it is only an answer if the
marker is derived from what the test *does* rather than from what somebody
remembered to call the file.

These tests are the teeth on that sentence:

- the collector must find a test that says it only in its code — a collector
  that looks at file names alone leaves it unmarked, and the first test fails;
- the marker must not be on everything — a collector that marks every file
  makes the second test fail.

The first assertion is made over the selection that actually ran, so it only
bites in a run that collects the family (the integration selection does). The
name-only rule is pinned separately on synthetic files, which every run
collects.
"""

import os
from pathlib import Path

import pytest

from tests.support.counting_session import (
    code_counts_statements,
    measures_query_counts,
    name_says_reads_once,
)

MARKER = "reads_once"


def _path_of(item: pytest.Item) -> str:
    path = getattr(item, "path", None)
    return os.fspath(path) if path is not None else ""


def _marked(item: pytest.Item) -> bool:
    return item.get_closest_marker(MARKER) is not None


def _content_only_files() -> set[str]:
    """Test files that join the family by their code and not by their name.

    Read from the tree rather than from the collected items, so the question
    "does such a file exist" has an answer even in a run that selected a subset.
    """
    here = Path(__file__).resolve().parent
    return {
        str(path)
        for path in here.rglob("test_*.py")
        if code_counts_statements(str(path)) and not name_says_reads_once(str(path))
    }


def test_a_test_that_says_it_only_in_code_is_in_the_family(
    request: pytest.FixtureRequest,
) -> None:
    """The content rule is load-bearing, not decoration.

    ``test_hot_path_queries.py`` gives nothing away in its file name and counts
    statements in its code. A collector that read names alone would leave it —
    and every future member of the family written by someone who never heard of
    the naming convention — out of `-m reads_once`.
    """
    content_only = _content_only_files()
    assert content_only, (
        "no test file in the tree proves the content rule is doing anything: "
        "every member of the family also announces itself in its file name"
    )
    collected = [
        item for item in request.session.items if _path_of(item) in content_only
    ]
    if not collected:
        pytest.skip("this selection collected none of the content-only members")
    unmarked = sorted({item.nodeid for item in collected if not _marked(item)})
    assert not unmarked, (
        "these tests count SQL statements but were not given the "
        f"{MARKER!r} marker — the collector is not reading their code:\n  "
        + "\n  ".join(unmarked)
    )


def test_nothing_outside_the_family_wears_the_marker(
    request: pytest.FixtureRequest,
) -> None:
    """A marker on everything says nothing, and `-m reads_once` would become
    "run the suite".

    The signature is checked against the same predicate the collector uses, so
    this fails the moment the collector stops deciding and starts stamping.
    """
    marked = [item for item in request.session.items if _marked(item)]
    if not marked:
        pytest.skip("this selection collected no family member")
    strays = sorted(
        {item.nodeid for item in marked if not measures_query_counts(_path_of(item))}
    )
    assert not strays, (
        f"these tests do not count SQL statements but carry {MARKER!r}:\n  "
        + "\n  ".join(strays)
    )


def test_the_name_rule_alone_would_miss_a_file_that_counts(tmp_path) -> None:
    """A file whose name says nothing, and whose code counts, is a member.

    Spelled out on synthetic files as well as on the live collection: this is
    the case a name-only rule gets wrong, and it is the case every new member
    of the family will be.
    """
    quiet = tmp_path / "test_quiet.py"
    quiet.write_text(
        "def test_one_query():\n"
        "    event.listen(engine, 'after_cursor_execute', record)\n",
        encoding="utf-8",
    )
    unrelated = tmp_path / "test_unrelated.py"
    unrelated.write_text("def test_nothing():\n    assert True\n", encoding="utf-8")

    assert code_counts_statements(str(quiet))
    assert not name_says_reads_once(str(quiet))
    assert measures_query_counts(str(quiet))
    assert not measures_query_counts(str(unrelated))
