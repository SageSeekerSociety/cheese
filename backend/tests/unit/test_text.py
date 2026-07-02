"""markdown_preview: clean notification previews (no dangling markdown)."""

from app.core.text import markdown_preview, truncate_words


def test_short_text_passes_through_with_whitespace_collapsed():
    assert markdown_preview("hello   world", 200) == "hello world"


def test_truncates_long_text_with_ellipsis():
    out = markdown_preview("a" * 300, 200)
    assert out.endswith("…")
    assert len(out) == 201  # 200 chars + ellipsis


def test_drops_bold_opener_left_unclosed_by_the_cut():
    # The cut lands after the opening ** of the last item; the opener must go so
    # it doesn't render as a literal **.
    text = "**one** **two** **three keeps going and going " + "x" * 200
    out = markdown_preview(text, 40)
    assert out.count("**") % 2 == 0  # balanced
    assert out.endswith("…")


def test_strips_trailing_marker_at_the_cut():
    out = markdown_preview("word `code and more " + "y" * 200, 30)
    assert "`" not in out or out.count("`") % 2 == 0
    assert out.endswith("…")


def test_mention_tokens_are_preserved():
    # Reference tokens are structural; the preview keeps them for the frontend
    # to render as chips.
    text = "分工：<@user-1> 负责后端，" + "详情" * 200
    out = markdown_preview(text, 50)
    assert "<@user-1>" in out


# --- truncate_words -------------------------------------------------------


def test_truncate_words_returns_all_words_when_under_limit():
    # Fewer words than n: everything survives, whitespace collapsed, no ellipsis.
    assert truncate_words("hello   world", 5) == "hello world"


def test_truncate_words_appends_ellipsis_when_cut():
    assert truncate_words("one two three four five", 3) == "one two three…"


def test_truncate_words_no_ellipsis_when_exactly_n():
    assert truncate_words("one two three", 3) == "one two three"


def test_truncate_words_empty_string_returns_empty():
    assert truncate_words("", 5) == ""
    assert truncate_words("   ", 5) == ""


def test_truncate_words_non_positive_n_returns_empty():
    assert truncate_words("one two three", 0) == ""
    assert truncate_words("one two three", -1) == ""
