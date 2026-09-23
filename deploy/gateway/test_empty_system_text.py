"""System blocks sent through Messages must not contain empty text."""

from unittest.mock import patch

from litellm.llms.anthropic.experimental_pass_through.messages.handler import (
    LiteLLMMessagesToCompletionTransformationHandler,
    ProviderConfigManager,
    anthropic_messages_handler,
)


def forward(system):
    with (
        patch.object(ProviderConfigManager, "get_provider_anthropic_messages_config", return_value=None),
        patch.object(
            LiteLLMMessagesToCompletionTransformationHandler,
            "anthropic_messages_handler",
            side_effect=lambda **kwargs: kwargs["system"],
        ),
    ):
        return anthropic_messages_handler(
            max_tokens=8,
            messages=[{"role": "user", "content": "Reply OK."}],
            model="anthropic/claude-3-5-sonnet-20241022",
            system=system,
        )


assert forward([{"type": "text", "text": ""}, {"type": "text", "text": "Answer briefly."}]) == [
    {"type": "text", "text": "Answer briefly."}
]
assert forward([{"type": "text", "text": "  "}]) is None
assert forward("Answer briefly.") == "Answer briefly."
print("PASS: empty system text removed; valid system text retained")
