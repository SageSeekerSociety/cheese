"""Exercise both request paths using the pinned adapter and deployment catalogue."""

import asyncio
import copy
from pathlib import Path

import litellm
import yaml
from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
    LiteLLMAnthropicMessagesAdapter,
)
from litellm.llms.deepseek.chat.transformation import DeepSeekChatConfig

IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="


async def main():
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    litellm.Router(model_list=config["model_list"])
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": IMAGE},
    }
    adapter = LiteLLMAnthropicMessagesAdapter()
    cases = [
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Describe this"}, image],
            }
        ],
        [
            {"role": "user", "content": "Read the screenshot"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read-1",
                        "name": "Read",
                        "input": {"file_path": "image.png"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-1",
                        "content": [{"type": "text", "text": "Screenshot"}, image],
                    }
                ],
            },
        ],
    ]
    provider = DeepSeekChatConfig()
    for messages in cases:
        translated = adapter.translate_anthropic_messages_to_openai(
            copy.deepcopy(messages), model="deepseek-flash"
        )
        for asynchronous in (False, True):
            kwargs = dict(
                model="deepseek-flash",
                messages=copy.deepcopy(translated),
                optional_params={},
                litellm_params={},
                headers={},
            )
            payload = (
                await provider.async_transform_request(**kwargs)
                if asynchronous
                else provider.transform_request(**kwargs)
            )
            pictures = [
                b
                for m in payload["messages"]
                if isinstance(m.get("content"), list)
                for b in m["content"]
                if b.get("type") == "image_url"
            ]
            assert len(pictures) == 1, payload
            assert pictures[0]["image_url"]["url"] == "data:image/png;base64," + IMAGE
    text = provider.transform_request(
        model="deepseek-chat",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        optional_params={},
        litellm_params={},
        headers={},
    )
    assert text["messages"][0]["content"] == "hello"
    print(
        "PASS: upload and Read images preserved, sync and async; legacy text preserved"
    )


if __name__ == "__main__":
    asyncio.run(main())
