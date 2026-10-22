"""「没有分母就没有比率」；摘要按字数截，截断处标出来。"""

import pytest

from app.domain.docs_site.excerpt import excerpt
from app.domain.feature_stats import stats


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [(None, 5, None), (1, None, None), (1, 0, None), (1, 4, 0.25)],
)
def test_ratio_has_no_value_without_a_base(numerator, denominator, expected):
    assert stats.ratio(numerator, denominator) == expected


def test_excerpt_keeps_text_exactly_at_the_limit():
    assert excerpt("abcde", 5) == "abcde"


def test_excerpt_cuts_longer_text_and_marks_the_cut():
    assert excerpt("abcd efgh", 5) == "abcd…"


def test_excerpt_collapses_whitespace_before_measuring():
    assert excerpt("  a\n\n b\tc  ", 5) == "a b c"
