"""Agent type library loader: Claude Code agents-style markdown + frontmatter."""

from pathlib import Path

import pytest

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
        "skills: research, writing\n---\npersona A",
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
    assert types["alpha"].mcp_servers == []
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


# The frontmatter a Claude Code user writes for a subagent's MCP servers
# (code.claude.com/docs/en/sub-agents, "mcpServers"): inline definitions keyed
# by name, and names of servers the session already has.
SUBAGENT_STYLE = """---
name: browser-tester
description: Tests features in a real browser
mcpServers:
  # Inline definition: scoped to this agent only
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
  - tracker:
      type: http
      url: https://mcp.example.test/mcp
  # Reference by name: a server the session already has
  - github
---
You test features in a browser.
"""


def test_a_type_declares_mcp_servers_as_a_claude_code_subagent_does(tmp_path: Path):
    (tmp_path / "browser-tester.md").write_text(SUBAGENT_STYLE, encoding="utf-8")
    tester = load_type_library(tmp_path)["browser-tester"]
    assert tester.mcp_servers == [
        {
            "playwright": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@playwright/mcp@latest"],
            }
        },
        {"tracker": {"type": "http", "url": "https://mcp.example.test/mcp"}},
        "github",
    ]
    assert set(tester.inline_servers()) == {"playwright", "tracker"}


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ("  - live:\n      type: ws\n      url: wss://x.test/mcp\n", "uses ws"),
        ("  - broken:\n      type: stdio\n", "has no command"),
        ("  - native:\n      command: x\n", "reserved"),
        ("  - one: {command: a}\n    two: {command: b}\n", "a name or"),
    ],
)
def test_a_server_a_room_cannot_run_is_refused_when_the_library_loads(
    tmp_path: Path, entry: str, problem: str
):
    (tmp_path / "t.md").write_text(
        f"---\nname: t\nmcpServers:\n{entry}---\nbody", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=problem):
        load_type_library(tmp_path)


def test_one_server_name_is_one_server_across_the_library(tmp_path: Path):
    """A project connects a remote server once, by name, for every teammate
    whose type declares it, so two types cannot mean two hosts by one name."""
    for name, url in (("a", "https://one.test/mcp"), ("b", "https://two.test/mcp")):
        (tmp_path / f"{name}.md").write_text(
            f"---\nname: {name}\nmcpServers:\n  - tracker:\n      url: {url}\n"
            "---\nbody",
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="defined differently"):
        load_type_library(tmp_path)


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
