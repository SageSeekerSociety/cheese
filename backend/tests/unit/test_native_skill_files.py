"""原生 skill 整份装船：容器和设备两条路上，目录里的东西都得齐。

A skill is a directory, not a markdown file. `documents` ships a script that
edits Word XML and five reference files that explain when to run it, and the
failure this file exists to prevent is the quiet one: SKILL.md arrives, tells
the agent to run `scripts/office.py`, and the script is not there. Both paths
carry `native_skill_files()`, so everything here is checked on that one dict.
"""

from pathlib import Path

import pytest

from app.domain.agent.skills import (
    _NATIVE_SKILL_SRC,
    _SHIPPED_NATIVE_SKILLS,
    _SKILL_FILE_SUFFIXES,
    SKILL_HEREDOC_MARKER,
    native_skill_files,
)


def _shipped() -> dict[str, str]:
    return native_skill_files()


def _files_in_tree() -> list[Path]:
    found = []
    for name in _SHIPPED_NATIVE_SKILLS:
        root = _NATIVE_SKILL_SRC / name
        if root.is_dir():
            found.extend(p for p in root.rglob("*") if p.is_file())
    return found


def test_every_shipped_skill_brings_its_instructions():
    """没有 SKILL.md 的目录，对 claude 来说等于不存在。"""
    for name in _SHIPPED_NATIVE_SKILLS:
        assert f"skills/{name}/SKILL.md" in _shipped(), name


def test_the_scripts_and_references_travel_with_it():
    """这些是这次改动存在的理由：改 Word 的脚本和它的说明必须一起到。"""
    shipped = _shipped()
    assert "skills/documents/scripts/office.py" in shipped
    assert "skills/documents/references/word.md" in shipped
    assert "skills/documents/references/reading.md" in shipped


def test_nothing_in_the_directory_is_left_behind_without_saying_so():
    """目录里多出来的文件要么装船，要么在这条测试里失败。

    The suffix list is what keeps a screenshot or a font from being written
    through a shell heredoc on a device -- it would arrive corrupted rather
    than fail. Adding one to a skill is therefore a decision, and this is where
    that decision gets made instead of in production.
    """
    shipped = _shipped()
    for path in _files_in_tree():
        relative = path.relative_to(_NATIVE_SKILL_SRC).as_posix()
        if path.suffix in _SKILL_FILE_SUFFIXES:
            assert f"skills/{relative}" in shipped, f"{relative} 没有装船"
        else:
            pytest.fail(
                f"{relative} 的后缀 {path.suffix!r} 不在 _SKILL_FILE_SUFFIXES 里，"
                "它现在到不了用户的机器上"
            )


def test_no_skill_file_ends_its_own_heredoc():
    """设备那条路把每个文件写进 <<'CHEESE_NATIVE_SKILL'，文件里出现这一行就截断。"""
    for name, content in _shipped().items():
        assert SKILL_HEREDOC_MARKER not in content, name


def test_keys_are_relative_to_the_config_directory():
    """绝对路径或者 `..` 会把文件写到用户的机器上另一个地方去。"""
    for name in _shipped():
        assert not name.startswith("/"), name
        assert ".." not in Path(name).parts, name
        assert name.startswith("skills/"), name


def test_the_editing_script_is_valid_python():
    """它是以文本形式发出去的，语法错要到用户机器上运行才发现。"""
    source = _shipped()["skills/documents/scripts/office.py"]
    compile(source, "office.py", "exec")


def test_skill_md_only_points_at_files_that_travel():
    """SKILL.md 里写 `references/xxx.md` 而文件不在，agent 照做只会扑空。"""
    shipped = _shipped()
    skill_md = shipped["skills/documents/SKILL.md"]
    for reference in (
        "references/reading.md",
        "references/word.md",
        "references/slides.md",
        "references/sheets.md",
        "references/pdf.md",
        "scripts/office.py",
    ):
        assert reference in skill_md, f"SKILL.md 没提到 {reference}"
        assert f"skills/documents/{reference}" in shipped, f"{reference} 没有装船"


def test_the_specialist_files_are_named_from_the_index():
    """参考文件是按需读的，索引里没有的那份永远不会被读到。"""
    shipped = _shipped()
    index = shipped["skills/documents/SKILL.md"]
    for name in shipped:
        if "/references/" in name:
            assert Path(name).name in index, f"{name} 在 SKILL.md 里没有入口"
