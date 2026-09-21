"""Agent type library loader: Claude Code agents-style markdown + frontmatter."""

from pathlib import Path

from app.domain.agent_type.library import (
    load_type_library,
    parse_list_value,
    parse_type_markdown,
    preset_types,
)


def test_parse_frontmatter_and_body():
    meta, body = parse_type_markdown(
        "---\nname: my-type\ntitle: 我的角色\ndescription: 简介\n---\n\n"
        "你是专家。\n多行正文。\n"
    )
    assert meta == {"name": "my-type", "title": "我的角色", "description": "简介"}
    assert body == "你是专家。\n多行正文。"


def test_parse_quoted_values_and_comments():
    meta, body = parse_type_markdown(
        "---\nname: \"quoted-type\"\n# a comment line\ntitle: '单引号'\n\n---\n"
        "body here"
    )
    assert meta["name"] == "quoted-type"
    assert meta["title"] == "单引号"
    assert body == "body here"


def test_parse_without_frontmatter_is_all_body():
    meta, body = parse_type_markdown("你是一个没有元数据的角色。\n")
    assert meta == {}
    assert body == "你是一个没有元数据的角色。"


def test_parse_unclosed_frontmatter_has_empty_body():
    meta, body = parse_type_markdown("---\nname: broken\ntitle: t")
    assert meta["name"] == "broken"
    assert body == ""


def test_list_values_are_comma_separated_with_blanks_dropped():
    assert parse_list_value("deploy, oncall ,, ") == ["deploy", "oncall"]
    assert parse_list_value("") == []


def test_load_type_library_from_dir(tmp_path: Path):
    (tmp_path / "alpha.md").write_text(
        "---\nname: alpha\ntitle: A\ndescription: d\n"
        "skills: research, writing\nmcp_servers: grafana\n---\npersona A",
        encoding="utf-8",
    )
    # No frontmatter name → filename stem is the type name.
    (tmp_path / "beta.md").write_text("persona B", encoding="utf-8")
    # Empty body → no system prompt → no agent → skipped.
    (tmp_path / "empty.md").write_text("---\nname: empty\n---\n", encoding="utf-8")

    types = load_type_library(tmp_path)
    assert set(types) == {"alpha", "beta"}
    assert types["alpha"].title == "A"
    assert types["alpha"].body == "persona A"
    assert types["alpha"].skills == ["research", "writing"]
    assert types["alpha"].mcp_servers == ["grafana"]
    assert types["beta"].title == "beta"


def test_a_type_says_nothing_about_how_it_runs(tmp_path: Path):
    """一个类型说的是角色。它连说「用哪个模型、哪个骨架」的字段都没有——写在
    frontmatter 里也进不来，所以一份抄旧格式的类型文件不会悄悄钉住部署的选择。
    """
    (tmp_path / "plain.md").write_text(
        "---\nname: plain\nmodel: claude-opus-5\nharness: codex\neffort: high\n"
        "---\n正文",
        encoding="utf-8",
    )

    plain = load_type_library(tmp_path)["plain"]
    assert not {"model", "effort", "harness"} & set(vars(plain))
    assert plain.body == "正文"
    assert plain.skills == []
    assert plain.mcp_servers == []


def test_load_type_library_missing_dir(tmp_path: Path):
    assert load_type_library(tmp_path / "nope") == {}


def test_presets_ship_with_the_platform():
    types = preset_types()
    assert set(types) >= {
        "fullstack-engineer",
        "academic-research",
        "product-design",
        "startup-founder",
    }
    assert types["fullstack-engineer"].title == "全栈工程"
    assert types["academic-research"].description != ""
