"""一条记忆超了单条上限，就不收；收的是平台原来那一版。

正文上限 1000 字，索引里新写的一行上限 150 字符。拦在对账里：会话写的那一版超了，
平台那一版留着，会话那一版和原因交回去，好让写的人改短再写。索引只量新写的行——
别人早先留下的一行长的，不挡这一次。
"""

from app.domain.memory.files import digest
from app.domain.memory.tree import sync_tree

_OLD = "---\nname: a\ndescription: 旧的\ntype: project\n---\n\n原来那一版。\n"


def _note(body: str) -> str:
    return f"---\nname: a\ndescription: 一条\ntype: project\n---\n\n{body}\n"


def _session_wrote(path: str, content: str, *, platform: str | None):
    """会话把 ``path`` 写成了 ``content``；平台这一份自上次以来没动过。"""
    scopes = {"project": {} if platform is None else {path.split("/")[1]: platform}}
    baseline = {} if platform is None else {path: digest(platform)}
    return sync_tree(scopes=scopes, disk={path: content}, baseline=baseline)


def test_a_body_at_the_limit_is_taken():
    long_enough = _note("字" * 1000)
    result = _session_wrote("project/a.md", long_enough, platform=_OLD)
    assert result.files == {"project/a.md": long_enough}
    assert result.rejected == {}


def test_a_body_over_the_limit_leaves_the_platform_version():
    result = _session_wrote("project/a.md", _note("字" * 1001), platform=_OLD)
    assert result.files == {"project/a.md": _OLD}
    assert result.baseline == {"project/a.md": digest(_OLD)}
    assert "project/a.md" in result.rejected


def test_a_new_memory_over_the_limit_is_not_created():
    result = _session_wrote("project/a.md", _note("字" * 1001), platform=None)
    assert result.files == {}
    assert "project/a.md" in result.rejected


def test_a_file_without_frontmatter_is_measured_whole():
    """frontmatter 写错不是绕过上限的办法。"""
    result = _session_wrote("project/a.md", "字" * 1001, platform=_OLD)
    assert result.files == {"project/a.md": _OLD}
    assert "project/a.md" in result.rejected


def test_a_new_index_line_over_the_limit_is_not_taken():
    index = "- [a](a.md) — 原来那一行\n"
    long_line = "- [b](b.md) — " + "长" * 150
    result = _session_wrote(
        "project/MEMORY.md", index + long_line + "\n", platform=index
    )
    assert result.files == {"project/MEMORY.md": index}
    assert "project/MEMORY.md" in result.rejected


def test_an_old_long_index_line_does_not_block_an_unrelated_edit():
    old_long = "- [a](a.md) — " + "长" * 180
    index = old_long + "\n"
    edited = index + "- [b](b.md) — 新加的一行\n"
    result = _session_wrote("project/MEMORY.md", edited, platform=index)
    assert result.files == {"project/MEMORY.md": edited}
    assert result.rejected == {}


def test_a_session_that_did_not_touch_a_long_memory_keeps_it():
    """上限拦的是这一次写的，不是平台上早就在的那一版。"""
    long_on_platform = _note("字" * 1500)
    result = sync_tree(
        scopes={"project": {"a.md": long_on_platform}},
        disk={"project/a.md": long_on_platform},
        baseline={"project/a.md": digest(long_on_platform)},
    )
    assert result.files == {"project/a.md": long_on_platform}
    assert result.rejected == {}
