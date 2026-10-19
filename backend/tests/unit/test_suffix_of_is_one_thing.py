"""A document's suffix is read one way, whichever module is asked.

Converting a document and recalculating a workbook both have to name the format
they were handed, and a user who names `预算.XLSX` must not get a different
answer depending on which of the two paths sees the file. One definition, so a
later change to what counts as a suffix cannot reach only one of them.
"""

import pytest

from app.domain.documents import convert, spreadsheet


def test_both_modules_read_the_suffix_with_one_function():
    assert spreadsheet.suffix_of is convert.suffix_of


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("预算.xlsx", ".xlsx"),
        ("faq.csv", ".csv"),
        ("预算.XLSX", ".xlsx"),
        ("README", ""),
        ("a.b.c", ".c"),
    ],
)
def test_both_modules_read_the_same_suffix_from_the_same_path(path, expected):
    assert convert.suffix_of(path) == expected
    assert spreadsheet.suffix_of(path) == expected
