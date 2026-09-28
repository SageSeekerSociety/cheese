"""索引超了会怎样：写入照旧成功，注入时按行截断，并且明说截断了。

预算是注入的预算，不是写入的闸。拒绝写入会把「这条记忆没记下来」和「索引该整理
了」混成一件事，而后者才是真的——所以这里量的是截断本身，以及那句警告在不在：
一段短的索引读起来就是「索引就这么长」，读到它的人不会去修它。
"""

from app.domain.memory.files import (
    INDEX_MAX_BYTES,
    INDEX_MAX_LINES,
    fit_index,
)


def _lines(count: int, *, width: int = 20) -> str:
    return "".join(
        f"- [条目{i:03d}](f{i:03d}.md) — {'x' * width}\n" for i in range(count)
    )


def test_an_index_inside_the_budget_is_not_touched():
    """一个字节都不动：多一次重排就是多一次「读取时改了内容」的机会。"""
    text = _lines(3)
    fitted, warning = fit_index(text)
    assert fitted == text
    assert warning is None


def test_an_index_over_the_line_budget_keeps_whole_lines():
    text = _lines(INDEX_MAX_LINES + 5)
    fitted, warning = fit_index(text)
    kept = fitted.splitlines()
    assert len(kept) == INDEX_MAX_LINES
    # 截断是取前几行，不是重新排版：留下的每一行都还是原来那一行。
    assert kept == text.splitlines()[:INDEX_MAX_LINES]
    assert fitted.endswith("\n")


def test_an_index_over_the_byte_budget_is_cut_even_when_short_on_lines():
    text = _lines(20, width=2000)
    assert len(text.encode()) > INDEX_MAX_BYTES
    fitted, warning = fit_index(text)
    assert len(fitted.encode()) <= INDEX_MAX_BYTES
    assert fitted.splitlines() == text.splitlines()[: len(fitted.splitlines())]


def test_the_warning_says_it_was_cut_and_how_to_fix_it():
    _, warning = fit_index(_lines(INDEX_MAX_LINES + 1))
    assert warning is not None
    assert "读不到" in warning
    assert f"{INDEX_MAX_LINES} 行" in warning
    assert f"{INDEX_MAX_BYTES // 1024}KB" in warning


def test_an_empty_index_stays_empty():
    assert fit_index("") == ("", None)
