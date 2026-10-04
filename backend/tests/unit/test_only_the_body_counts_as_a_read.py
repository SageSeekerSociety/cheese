"""「读了一次记忆正文」这条判据。

三个条件同时成立才算：工具是 `Read`、路径在 `.cheese/memory/` 下面、那个文件不是
`MEMORY.md`。这条数是一个试点要回答的问题（一周里正文的读取是不是接近 0），所以
判宽了会把索引那部分每轮注入的读也算进去 —— 那个数会永远不是 0，试点就白做了。

判据有两份实现：`reads.is_body_read`（纯函数）和 `reads.body_reads` 里那两条 SQL
谓词（要在数据库里跑）。这里钉的是纯函数那一份；两份是否一致由
`tests/integration/test_the_body_reads_are_counted.py` 里同一批输入喂两边来钉。
"""

import pytest

from app.domain.memory.reads import is_body_read

_HOME = "/home/cheese"


def test_a_read_of_a_body_file_counts():
    assert is_body_read("Read", f"{_HOME}/.cheese/memory/project/release-steps.md")
    assert is_body_read("Read", f"{_HOME}/.cheese/memory/private/alice/vim.md")


@pytest.mark.parametrize("tool", ["Bash", "Write", "Edit", "Grep", "read", ""])
def test_only_the_read_tool_counts(tool):
    """写进去的不算读：这条数回答的是「有没有人翻开」，不是「有没有人碰过」。"""
    path = f"{_HOME}/.cheese/memory/project/release-steps.md"
    assert not is_body_read(tool, path)


def test_reading_something_else_does_not_count():
    assert not is_body_read("Read", f"{_HOME}/work/wt-dream/CLAUDE.md")
    assert not is_body_read("Read", "/etc/hosts")


def test_the_index_is_not_a_body_file():
    """索引每轮注入，读它不算翻正文。少了这一条，这个数永远不会是 0。"""
    assert not is_body_read("Read", f"{_HOME}/.cheese/memory/project/MEMORY.md")
    assert not is_body_read("Read", f"{_HOME}/.cheese/memory/private/alice/MEMORY.md")


def test_a_name_that_merely_ends_like_the_index_still_counts():
    """排除的是那个**文件**，不是「名字里有 MEMORY.md 的文件」。

    `MEMORY.md.bak` 是备份，`MEMORY.md` 才是索引。
    """
    assert is_body_read("Read", f"{_HOME}/.cheese/memory/project/MEMORY.md.bak")
    assert is_body_read("Read", f"{_HOME}/.cheese/memory/project/MEMORY.mdnotes.md")


def test_a_directory_that_merely_starts_like_the_memory_dir_does_not_count():
    """判据是那一段路径，不是前缀：`memory-notes` 不是记忆目录。"""
    assert not is_body_read("Read", f"{_HOME}/.cheese/memory-notes/project/x.md")
    assert not is_body_read("Read", f"{_HOME}/.cheese/memory")


def test_the_agent_directory_is_part_of_the_path_too():
    """会话机上的家目录不一样，所以判据是「含有那一段」而不是「以它开头」。"""
    assert is_body_read("Read", "/root/.cheese/memory/project/a.md")
    assert is_body_read("Read", "/srv/x/.cheese/memory/private/bob/b.md")


def test_a_windows_style_separator_still_finds_the_directory():
    assert is_body_read("Read", r"C:\Users\cheese\.cheese\memory\project\a.md")
    assert not is_body_read("Read", r"C:\Users\cheese\.cheese\memory\project\MEMORY.md")


@pytest.mark.parametrize(
    ("tool", "detail"),
    [
        (None, f"{_HOME}/.cheese/memory/project/a.md"),
        ("Read", None),
        ("Read", 42),
        (None, None),
    ],
)
def test_a_record_without_those_two_fields_does_not_count(tool, detail):
    """老的事件块（`meta` 是别的形状）不该让这个数当场炸掉。"""
    assert not is_body_read(tool, detail)
