"""Unit tests for agent streaming helpers — no SDK, no network."""

from app.domain.agent.chat import _build_system_prompt
from app.domain.agent.service import _extract_text_delta


def test_extract_text_delta_from_content_block_delta():
    event = {
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": "hello"},
    }
    assert _extract_text_delta(event) == "hello"


def test_extract_text_delta_ignores_other_events():
    assert _extract_text_delta({"type": "message_start"}) is None
    assert (
        _extract_text_delta(
            {"type": "content_block_delta", "delta": {"type": "thinking_delta"}}
        )
        is None
    )


def test_build_system_prompt_without_extras_returns_base():
    assert _build_system_prompt("base", "", None, []) == "base"


def test_build_system_prompt_includes_skills_doc_and_memory():
    prompt = _build_system_prompt(
        "base", "SKILL TEXT", "## 目标\n做推荐", ["fact A", "fact B"]
    )
    assert "base" in prompt
    assert "SKILL TEXT" in prompt
    assert "做推荐" in prompt
    assert "- fact A" in prompt
    assert "- fact B" in prompt


def test_build_system_prompt_includes_expert_role():
    prompt = _build_system_prompt("base", "", None, [], role="你是学术研究导师")
    assert "你是学术研究导师" in prompt


def test_role_description_lookup():
    from app.domain.agent.roles import role_description

    assert role_description(None) is None
    assert role_description("unknown-role") is None
    assert "学术" in (role_description("academic-research") or "")


def test_chipify_paths_wraps_bare_relative_paths():
    """B2 (引用语法遵循): memory facts injected into the prompt must model the
    correct <&path> form; URLs / absolute paths / already-wrapped stay put."""
    from app.domain.agent.chat import _chipify_paths

    assert (
        _chipify_paths("根因在 backend/app/domain/agent/chat.py 第 932 行")
        == "根因在 <&backend/app/domain/agent/chat.py> 第 932 行"
    )
    assert (
        _chipify_paths("frontend/src/components/DocPanel.vue:753 的问题")
        == "<&frontend/src/components/DocPanel.vue>:753 的问题"
    )
    for untouched in (
        "已包 <&backend/app/main.py> 不动",
        "见 http://a.com/b/c.py 链接",
        "绝对路径 /usr/bin/python3.13 不包",
        "DocPanel.vue:753 无斜杠不包",
    ):
        assert _chipify_paths(untouched) == untouched
