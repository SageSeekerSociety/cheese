"""Known debt is a ceiling, never an xfail — because an xfail here is a red job.

``pytest`` reports an xfailed case as **skipped**, and
``backend/scripts/assert_suite_ran.py`` fails a job on any skipped case, by
design: a suite that cannot meet its environment must be loud rather than
green. So the marker cannot record debt in this repository. It records it by
turning the job red somewhere nobody looks while the suite is green on a
laptop, which is exactly the shape of the outage that led here: three
``@pytest.mark.xfail`` markers went in as a way to say "known N+1, batch it
later" and reddened every backend integration shard of the main batch.

Debt that is known and not yet paid is written as a ceiling instead
(``counting_session.assert_reads_within_ceiling``): the count measured now is
asserted on every run, so the test is green where the endpoint stands, red the
moment it reads one row more, and the ceiling only comes down.

The scan reads the AST rather than the text, because saying *why* the marker is
banned — as this docstring does, and as ``support/counting_session.py`` does —
must not itself be a violation. Two tests:

- the tree carries no xfail declaration, except the one place a quarantine
  entry becomes one (``conftest.py``, keyed by ``tests/quarantine.txt``, where
  the gate counts the case as run because the line names the issue that owns
  the fix);
- the scan reports a declaration when there is one, proved on a synthetic file.
  A scan that matches nothing would pass this file for the wrong reason.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

#: The one home the marker is allowed: the quarantine application in the root
#: ``tests/conftest.py``. One exact path, not the name ``conftest.py`` — the
#: three nested ones are ordinary files and stay inside the scan.
#: ``test_the_exemption_cannot_outlive_its_mechanism`` below keeps it from
#: becoming a general licence.
ALLOWED_HOME = TESTS / "conftest.py"

#: ``xfail`` reached as an attribute covers every spelling the marker has —
#: ``pytest.mark.xfail``, ``mark.xfail`` after ``from pytest import mark``, and
#: the imperative ``pytest.xfail("…")``, which also ends as a skip. A bare
#: ``Name`` is added by the caller when the file imported it by name.
ATTR = "xfail"


def _declarations(tree: ast.AST, *, imported_by_name: bool) -> list[ast.AST]:
    """Every expression in ``tree`` that declares or raises an xfail."""
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == ATTR:
            found.append(node)
        elif imported_by_name and isinstance(node, ast.Name) and node.id == ATTR:
            found.append(node)
    return found


def _imports_xfail_by_name(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pytest":
            if any(alias.name == ATTR for alias in node.names):
                return True
    return False


def _offenders(path: Path) -> list[int]:
    """Line numbers in ``path`` that declare an xfail, or none if it is clean.

    An unparsable file reports nothing rather than failing: this scan is about
    the marker, and a syntax error is what the suite's own collection reports.

    The text is read first because parsing every file under ``tests/`` took
    longer than the pure layer's ceiling once this test ran in the whole-tree
    guard set. An AST node whose attribute or name is ``xfail`` necessarily
    has that text in its source, so skipping files without it cannot change
    the verdict; prose that names the marker still parses and still passes.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if ATTR not in text:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return sorted(
        node.lineno
        for node in _declarations(tree, imported_by_name=_imports_xfail_by_name(tree))
    )


def _tree_files() -> list[Path]:
    return sorted(TESTS.rglob("*.py"))


def test_no_test_declares_an_xfail() -> None:
    """A declaration anywhere but the quarantine application reds the job.

    Put one in a test — ``@pytest.mark.xfail(reason="known N+1")`` — and this
    fails naming the file and the line, before the integration shards find out
    the expensive way.
    """
    offenders = [
        f"{path.relative_to(TESTS)}:{line}"
        for path in _tree_files()
        if path != ALLOWED_HOME
        for line in _offenders(path)
    ]
    assert not offenders, (
        "these declare pytest.mark.xfail; an xfailed case is reported as skipped "
        "and scripts/assert_suite_ran.py fails the job on a skip. Record known "
        "debt as a ceiling (counting_session.assert_reads_within_ceiling) or "
        "quarantine it in tests/quarantine.txt with the issue that owns it:\n  "
        + "\n  ".join(offenders)
    )


def test_the_exemption_cannot_outlive_its_mechanism() -> None:
    """``conftest.py`` is exempt because of quarantine, and only for as long.

    Delete the quarantine mechanism and the exemption has to go with it in the
    same change — otherwise the one name the scan does not read becomes the
    place an xfail hides.
    """
    assert ALLOWED_HOME.exists()
    assert "_QUARANTINE" in ALLOWED_HOME.read_text(encoding="utf-8"), (
        f"{ALLOWED_HOME.name} is exempt from the xfail scan because it turns "
        "each _QUARANTINE entry into one; that mechanism is gone, so the "
        "exemption is a hole. Drop ALLOWED_HOME from this file or bring the "
        "mechanism back."
    )
    listing = (TESTS / "quarantine.txt").read_text(encoding="utf-8")
    assert "issue" in listing.lower(), (
        "tests/quarantine.txt no longer says that each entry names the issue "
        "that owns the fix, which is what lets the gate count a quarantined "
        "case as run"
    )


def test_the_scan_reports_a_planted_declaration(tmp_path: Path) -> None:
    """Proof the scan bites, on the two spellings it has to catch.

    Without this, a scanner that matched nothing — a renamed attribute, a walk
    that visits no file — would leave the rule above green and unenforced.
    """
    marker = tmp_path / "test_marks_it.py"
    marker.write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.xfail(strict=True, reason='known N+1')\n"
        "def test_thing():\n"
        "    assert False\n",
        encoding="utf-8",
    )
    assert _offenders(marker) == [3]

    imperative = tmp_path / "test_raises_it.py"
    imperative.write_text(
        "import pytest\n\ndef test_thing():\n    pytest.xfail('not implemented yet')\n",
        encoding="utf-8",
    )
    assert _offenders(imperative) == [4]

    imported = tmp_path / "test_imported_name.py"
    imported.write_text(
        "from pytest import mark, xfail\n"
        "\n"
        "@mark.xfail\n"
        "def test_a():\n"
        "    assert False\n"
        "\n"
        "def test_b():\n"
        "    xfail('later')\n",
        encoding="utf-8",
    )
    assert _offenders(imported) == [3, 8]


def test_the_scan_does_not_read_prose(tmp_path: Path) -> None:
    """Saying the marker is banned is not declaring it.

    The docstrings that explain this rule name ``pytest.mark.xfail`` — this
    file's own, and ``support/counting_session.py``'s. A text scan would redden
    the explanation of the rule, which is how a rule like this gets deleted.
    """
    prose = tmp_path / "test_explains.py"
    prose.write_text(
        '"""Why pytest.mark.xfail is banned here (see the ceiling helper)."""\n'
        "\n"
        "NOTE = 'pytest.mark.xfail'\n"
        "\n"
        "\n"
        "def test_clean():\n"
        "    assert NOTE\n",
        encoding="utf-8",
    )
    assert _offenders(prose) == []
