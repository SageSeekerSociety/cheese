"""Anthropic Messages must not forward empty system or nested result text."""

from unittest.mock import patch

from litellm.llms.anthropic.experimental_pass_through.messages.handler import (
    LiteLLMMessagesToCompletionTransformationHandler,
    ProviderConfigManager,
    anthropic_messages_handler,
)


def forward(system=None, messages=None):
    with (
        patch.object(ProviderConfigManager, "get_provider_anthropic_messages_config", return_value=None),
        patch.object(
            LiteLLMMessagesToCompletionTransformationHandler,
            "anthropic_messages_handler",
            side_effect=lambda **kwargs: (kwargs["messages"], kwargs["system"]),
        ),
    ):
        return anthropic_messages_handler(
            max_tokens=8,
            messages=messages or [{"role": "user", "content": "Reply OK."}],
            model="anthropic/claude-3-5-sonnet-20241022",
            system=system,
        )


assert forward([{"type": "text", "text": ""}, {"type": "text", "text": "Answer briefly."}])[1] == [
    {"type": "text", "text": "Answer briefly."}
]
assert forward([{"type": "text", "text": "  "}])[1] is None
assert forward("Answer briefly.")[1] == "Answer briefly."
tool_history = [
    {"role": "assistant", "content": [{"type": "tool_use", "id": "tool1", "name": "test", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool1", "content": [
        {"type": "text", "text": ""}, {"type": "text", "text": "done"},
    ]}]},
    {"role": "user", "content": "Reply OK."},
]
forwarded, _ = forward(messages=tool_history)
assert forwarded[1]["content"][0]["content"] == [{"type": "text", "text": "done"}], forwarded
assert tool_history[1]["content"][0]["content"][0]["text"] == ""
print("PASS: empty system and nested tool-result text removed; valid content retained")
