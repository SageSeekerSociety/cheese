"""What counts as an input a session was sent, the part of its record kept past
the thirty days: what a person or the platform said into it, in each harness's
own shape, and not the agent's own prompt to a subagent."""

from app.domain.session_record.retention import is_input


def test_each_harness_s_inputs_are_inputs():
    claude = {"type": "user", "isReplay": True, "message": {"content": "fix it"}}
    codex = {
        "method": "item/completed",
        "params": {"threadId": "main", "item": {"type": "userMessage", "id": "u1"}},
    }
    pi = {"type": "message", "message": {"role": "user", "content": "fix it"}}
    assert is_input(claude)
    assert is_input(codex)
    assert is_input(pi)


def test_tool_results_and_the_agent_s_own_prompts_are_not():
    tool_result = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "ok"}]},
    }
    codex_child = {
        "method": "item/completed",
        "params": {"threadId": "child", "item": {"type": "userMessage", "id": "u2"}},
    }
    pi_subagent = {
        "type": "message",
        "subagent": "explorer",
        "message": {"role": "user", "content": "look"},
    }
    assistant = {"type": "message", "message": {"role": "assistant"}}
    assert not is_input(tool_result)
    assert not is_input(codex_child, {"child"})
    assert not is_input(pi_subagent)
    assert not is_input(assistant)
