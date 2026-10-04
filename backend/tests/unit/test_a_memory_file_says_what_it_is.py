"""一条记忆文件的形状：能读成什么，拒绝什么，路径能走到哪儿。

`files.py` 是纯的，所以「什么算一条合法的记忆」可以直接问，不用起会话、不用起库。
写记忆的是 agent 自己，它写坏一个文件的时候，这里就是那道闸——拒绝的理由要说得
出是哪一条不对，否则它改不对。
"""

import pytest

from app.domain.memory.files import (
    INDEX_NAME,
    MemoryFile,
    MemoryFileError,
    MemoryFileScope,
    MemoryType,
    check_path,
    check_scoped_path,
    digest,
    parse_index,
    parse_memory_file,
    scoped_prefix,
)

_GOOD = """---
name: integration-tests-hit-a-real-db
description: 集成用例打真的 Postgres，不要拿 sqlite 替
type: project
---

postgres 有 sqlite 没有的类型，替掉之后用例会绿而线上会红。
"""


def test_a_memory_file_reads_back_into_its_four_parts():
    memory = parse_memory_file(_GOOD)
    assert memory.name == "integration-tests-hit-a-real-db"
    assert memory.description == "集成用例打真的 Postgres，不要拿 sqlite 替"
    assert memory.type is MemoryType.project
    assert memory.body.startswith("postgres 有 sqlite")


def test_writing_it_back_out_gives_the_same_file():
    """索引行和文件都由同一个对象生成：两种渲染不能各说一套。"""
    memory = parse_memory_file(_GOOD)
    again = parse_memory_file(memory.text())
    assert again == memory
    assert memory.index_line("integration-tests-hit-a-real-db.md") == (
        "- [integration-tests-hit-a-real-db](integration-tests-hit-a-real-db.md)"
        " — 集成用例打真的 Postgres，不要拿 sqlite 替"
    )


@pytest.mark.parametrize(
    "text, reason",
    [
        ("没有 frontmatter 的正文", "frontmatter"),
        ("---\nname: a\ntype: project\n---\n正文\n", "description"),
        ("---\nname: a\ndescription: d\n---\n正文\n", "type"),
        ("---\nname: a\ndescription: d\ntype: 随便\n---\n正文\n", "type 只能是"),
        ("---\nname: NotKebab\ndescription: d\ntype: user\n---\n正文\n", "kebab"),
        ("---\nname: a\ndescription: d\ntype: user\n---\n\n", "正文"),
        ("---\nname: a\ndescription d\ntype: user\n---\n正文\n", "key: value"),
    ],
)
def test_a_file_that_is_not_a_memory_is_refused_with_a_reason(text, reason):
    with pytest.raises(MemoryFileError) as exc:
        parse_memory_file(text)
    assert reason in str(exc.value)


def test_an_empty_file_has_no_index_entries():
    assert parse_index("") == []


def test_index_lines_are_read_with_their_path_and_hook():
    entries = parse_index(
        "- [标题](a.md) — 一句钩子\n"
        "- [标题](b.md) - 破折号也认\n"
        "- [标题](c.md)\n"
        "这一行不是索引，原样留在文件里\n"
    )
    assert [(e.path, e.hook) for e in entries] == [
        ("a.md", "一句钩子"),
        ("b.md", "破折号也认"),
        ("c.md", ""),
    ]


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "sub/a.md", "../a.md", "a/b.md", "a\\b.md", "a.txt", "A.md", ""],
)
def test_a_path_that_is_not_one_file_next_to_the_index_is_refused(path):
    with pytest.raises(MemoryFileError):
        check_path(path)


def test_the_index_itself_is_a_legal_path_even_though_it_is_not_a_slug():
    assert check_path(INDEX_NAME) == INDEX_NAME
    assert check_path("answer-first.md") == "answer-first.md"


def test_a_scoped_path_says_which_tree_it_is_in():
    assert check_scoped_path("project/a.md") == ("project", "a.md")
    assert check_scoped_path("private/alice/a.md") == ("private/alice", "a.md")


@pytest.mark.parametrize(
    "path",
    [
        "a.md",
        "/project/a.md",
        "private/a.md",
        "private/alice/bob/a.md",
        "project/sub/a.md",
    ],
)
def test_a_scoped_path_with_the_wrong_number_of_parts_is_refused(path):
    with pytest.raises(MemoryFileError):
        check_scoped_path(path)


def test_a_prefix_is_per_scope_and_private_needs_an_owner():
    assert scoped_prefix(MemoryFileScope.project, None) == "project"
    assert scoped_prefix(MemoryFileScope.private, "alice") == "private/alice"
    with pytest.raises(MemoryFileError):
        scoped_prefix(MemoryFileScope.private, None)


def test_the_digest_tells_two_versions_apart():
    """回写靠它判「这一版是谁改的」，所以同一份内容必须给出同一个指纹。"""
    assert digest("正文") == digest("正文")
    assert digest("正文") != digest("正文 ")
    assert len(digest("正文")) == 16
    assert digest("") != digest("\n")


def test_a_memory_file_is_a_plain_value():
    """两处都拿它当值用（索引一行、文件一份），所以要能直接比。"""
    memory = MemoryFile(name="a", description="d", type=MemoryType.user, body="正文")
    assert memory == parse_memory_file(memory.text())
