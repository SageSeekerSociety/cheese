"""Role library loader: Claude Code agents-style markdown + YAML frontmatter."""

from pathlib import Path

from app.domain.agent.roles import (
    builtin_roles,
    load_role_library,
    parse_role_markdown,
    role_description,
)


def test_parse_frontmatter_and_body():
    meta, body = parse_role_markdown(
        "---\nname: my-role\ntitle: 我的角色\ndescription: 简介\n---\n\n"
        "你是专家。\n多行正文。\n"
    )
    assert meta == {"name": "my-role", "title": "我的角色", "description": "简介"}
    assert body == "你是专家。\n多行正文。"


def test_parse_quoted_values_and_comments():
    meta, body = parse_role_markdown(
        "---\nname: \"quoted-role\"\n# a comment line\ntitle: '单引号'\n\n---\n"
        "body here"
    )
    assert meta["name"] == "quoted-role"
    assert meta["title"] == "单引号"
    assert body == "body here"


def test_parse_without_frontmatter_is_all_body():
    meta, body = parse_role_markdown("你是一个没有元数据的角色。\n")
    assert meta == {}
    assert body == "你是一个没有元数据的角色。"


def test_parse_unclosed_frontmatter_has_empty_body():
    meta, body = parse_role_markdown("---\nname: broken\ntitle: t")
    assert meta["name"] == "broken"
    assert body == ""


def test_load_role_library_from_dir(tmp_path: Path):
    (tmp_path / "alpha.md").write_text(
        "---\nname: alpha\ntitle: A\ndescription: d\n---\npersona A",
        encoding="utf-8",
    )
    # No frontmatter name → filename stem is the role name.
    (tmp_path / "beta.md").write_text("persona B", encoding="utf-8")
    # Empty body → no persona → skipped.
    (tmp_path / "empty.md").write_text("---\nname: empty\n---\n", encoding="utf-8")

    roles = load_role_library(tmp_path)
    assert set(roles) == {"alpha", "beta"}
    assert roles["alpha"].title == "A"
    assert roles["alpha"].body == "persona A"
    assert roles["beta"].title == "beta"


def test_load_role_library_missing_dir(tmp_path: Path):
    assert load_role_library(tmp_path / "nope") == {}


def test_builtin_presets_migrated():
    roles = builtin_roles()
    assert set(roles) >= {
        "fullstack-engineer",
        "academic-research",
        "product-design",
        "startup-founder",
    }
    assert roles["fullstack-engineer"].title == "全栈工程"
    assert roles["academic-research"].description != ""


def test_role_description_builtin_lookup():
    assert role_description(None) is None
    assert role_description("unknown-role") is None
    assert "学术" in (role_description("academic-research") or "")
