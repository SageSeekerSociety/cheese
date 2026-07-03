"""AgentEvent wire (de)serialization — backend ⇄ cheesed node (design v2 R2)."""

from app.domain.agent.service import (
    AgentDelta,
    AgentMessage,
    AgentResult,
    AgentToolUse,
    AgentUsage,
    event_from_dict,
    event_to_dict,
)


def _roundtrip(event):
    return event_from_dict(event_to_dict(event))


def test_delta_roundtrip():
    r = _roundtrip(AgentDelta(text="你好"))
    assert isinstance(r, AgentDelta) and r.text == "你好"


def test_message_roundtrip():
    # The discrete-message boundary must survive the wire (a remote cheesed
    # node's turns land Slack-style messages just like local ones).
    r = _roundtrip(AgentMessage(text="第一条完整消息"))
    assert isinstance(r, AgentMessage) and r.text == "第一条完整消息"


def test_tool_roundtrip():
    r = _roundtrip(AgentToolUse(name="Bash", input={"command": "ls"}))
    assert isinstance(r, AgentToolUse)
    assert r.name == "Bash" and r.input == {"command": "ls"}


def test_result_roundtrip_with_usage():
    usage = AgentUsage(model="glm-5.2", input_tokens=10, output_tokens=5, cost_usd=0.01)
    r = _roundtrip(AgentResult(text="done", session_id="s1", usage=usage))
    assert isinstance(r, AgentResult)
    assert r.text == "done" and r.session_id == "s1"
    assert r.usage.model == "glm-5.2" and r.usage.total_tokens == 15


def test_result_roundtrip_without_usage():
    r = _roundtrip(AgentResult(text="x", session_id=None, usage=None))
    assert r.session_id is None and r.usage is None
