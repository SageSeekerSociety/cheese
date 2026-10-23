"""Run inside the pinned gateway image to check its real request transformation.

Usage: check_config.py <config.yaml>. The file supplies the custom providers;
the deployments are built here.
"""

import asyncio
import copy
import json
from datetime import datetime, timezone
from math import isclose
from pathlib import Path
import sys
from unittest.mock import Mock

import httpx
import litellm
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from litellm.llms.anthropic.experimental_pass_through.messages.transformation import (
    AnthropicMessagesConfig,
)
import yaml
from litellm.proxy.pass_through_endpoints.llm_provider_handlers import (
    anthropic_passthrough_logging_handler,
)
from litellm.proxy.types_utils.utils import get_instance_fn
from litellm.proxy.utils import create_model_info_response
from litellm.utils import custom_llm_setup


# Deployments the checks below build for themselves. config.yaml declares no
# models (every model is a row in the gateway database, edited on the admin
# page, and the backend admin service holds those rows to the pricing rules),
# so what this script checks is the pinned LiteLLM's behaviour on rows shaped
# like the real ones, not any real row.
DEEPSEEK_WINDOW = 920000
KIMI = {
    "model_name": "kimi-k3",
    "litellm_params": {
        "model": "anthropic/kimi-k3",
        "api_base": "https://api.moonshot.cn/anthropic",
        "api_key": "check",
        # RMB 20 input, 100 output, 2 cache read, 20 cache write (5m), 40 (1h)
        # per million, at RMB 7.1/USD.
        "input_cost_per_token": 0.000002816902,
        "output_cost_per_token": 0.000014084508,
        "cache_read_input_token_cost": 0.000000281691,
        "cache_creation_input_token_cost": 0.000002816902,
        "cache_creation_input_token_cost_above_1hr": 0.000005633803,
    },
    "model_info": {"supports_output_config": True, "supports_max_reasoning_effort": True},
}
MIMO = {
    "model_name": "mimo-v2.6-pro",
    "litellm_params": {
        "model": "anthropic/mimo-v2.6-pro",
        "api_base": "https://token-plan-cn.xiaomimimo.com/anthropic",
        "api_key": "check",
        "input_cost_per_token": 0.000000422535,
        "output_cost_per_token": 0.000000845070,
    },
    "model_info": {
        "supports_reasoning": True,
        "supports_vision": True,
        "supports_mid_conversation_system": False,
    },
}
DEEPSEEK = {
    "model_name": "deepseek-flash",
    "litellm_params": {
        "model": "deepseek/deepseek-flash",
        "api_key": "check",
        "input_cost_per_token": 0.0000003,
        "output_cost_per_token": 0.0000012,
    },
    "model_info": {
        "supports_reasoning": True,
        "supports_adaptive_thinking": True,
        "supports_output_config": True,
        "max_input_tokens": DEEPSEEK_WINDOW,
    },
}
PRICE_ONLY = {
    "model_name": "claude-sonnet-5",
    "litellm_params": {
        "model": "anthropic/claude-sonnet-5",
        "input_cost_per_token": 0.000002,
        "output_cost_per_token": 0.00001,
        "cache_read_input_token_cost": 0.0000002,
        "cache_creation_input_token_cost": 0.0000025,
        "cache_creation_input_token_cost_above_1hr": 0.000004,
    },
    "model_info": {"blocked": True},
}


def main() -> None:
    config = yaml.safe_load(Path(sys.argv[1]).read_text())
    # The proxy registers these before it builds its router; a row on one of
    # them names a provider LiteLLM does not know otherwise.
    litellm.custom_provider_map = [
        {"provider": item["provider"], "custom_handler": get_instance_fn(item["custom_handler"])}
        for item in config["litellm_settings"].get("custom_provider_map") or []
    ]
    custom_llm_setup()
    router = litellm.Router(model_list=copy.deepcopy([KIMI, MIMO, DEEPSEEK, PRICE_ONLY]))
    request = {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }
    transformed = copy.deepcopy(request)
    AnthropicMessagesConfig._translate_adaptive_effort_for_non_adaptive_model(
        model="deepseek-flash",
        optional_params=transformed,
        max_tokens=32000,
        custom_llm_provider="deepseek",
    )
    assert transformed == request, f"Thinking settings were changed: {transformed}"
    # K3's Messages API uses output_config.effort, not adaptive thinking.
    # Exercise the full pinned adapter with the image/tool history callers send.
    messages = [
        {"role": "user", "content": "Inspect the screenshot"},
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
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": (
                                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAw"
                                    "CAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="
                                ),
                            },
                        }
                    ],
                }
            ],
        },
    ]
    tools = [{"name": "Read", "input_schema": {"type": "object"}}]
    for effort in ("low", "high", "max"):
        for adaptive in (False, True):
            params = {
                "max_tokens": 4096,
                "stream": True,
                "tools": copy.deepcopy(tools),
                "output_config": {"effort": effort},
            }
            if adaptive:
                params["thinking"] = {"type": "adaptive"}
            payload = AnthropicMessagesConfig().transform_anthropic_messages_request(
                model="kimi-k3",
                messages=copy.deepcopy(messages),
                anthropic_messages_optional_request_params=params,
                litellm_params={},
                headers={},
            )
            assert payload["output_config"] == {"effort": effort}, payload
            assert "thinking" not in payload, payload
            assert payload["messages"] == messages, payload
            assert payload["tools"] == tools and payload["stream"] is True, payload
    # MiMo accepts enabled thinking; the adapter must translate adaptive callers
    # while preserving the image and tool history used by the coding agent.
    mimo_payload = AnthropicMessagesConfig().transform_anthropic_messages_request(
        model="mimo-v2.6-pro",
        messages=copy.deepcopy(messages),
        anthropic_messages_optional_request_params={
            "max_tokens": 4096,
            "stream": True,
            "tools": copy.deepcopy(tools),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        },
        litellm_params={},
        headers={},
    )
    assert mimo_payload["thinking"]["type"] == "enabled", mimo_payload
    assert 0 < mimo_payload["thinking"]["budget_tokens"] < 4096, mimo_payload
    assert "output_config" not in mimo_payload, mimo_payload
    assert mimo_payload["messages"] == messages, mimo_payload
    assert mimo_payload["tools"] == tools and mimo_payload["stream"] is True
    # A price-only row carries the prices of a Claude model the platform's
    # subscription serves; the backend reads them back through /model/info to
    # bill that traffic. A blocked deployment must never route, and its prices,
    # the one-hour cache write included, must reach the cost map.
    price_only = [
        item
        for item in router.model_list
        if item.get("model_info", {}).get("blocked") is True
    ]
    assert len(price_only) == 1, price_only
    for deployment in price_only:
        name = deployment["model_name"]
        assert router._is_model_fully_blocked(name), f"{name}: must not route"
        try:
            asyncio.run(
                router.async_get_available_deployment(
                    model=name,
                    messages=[{"role": "user", "content": "x"}],
                    request_kwargs={},
                )
            )
        except (TypeError, AttributeError):
            raise  # the check itself is broken, not a refusal
        except Exception:  # noqa: BLE001 - the router's refusal is the point
            pass
        else:
            raise AssertionError(f"{name}: a price-only entry was routed")
        info = litellm.model_cost[deployment["model_info"]["id"]]
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "cache_read_input_token_cost",
            "cache_creation_input_token_cost",
            "cache_creation_input_token_cost_above_1hr",
        ):
            assert info.get(field, 0) > 0, f"{name}: {field} must be priced"
    # Messages usage reports uncached, cache-read and cache-written input separately.
    # Verify the bill, including the one-hour cache-write rate, not just metadata.
    kimi = next(item for item in router.model_list if item["model_name"] == "kimi-k3")
    handler = anthropic_passthrough_logging_handler.AnthropicPassthroughLoggingHandler
    for ttl, write_rate in (
        ("ephemeral_5m_input_tokens", 20),
        ("ephemeral_1h_input_tokens", 40),
    ):
        response = {
            "id": "cost-check",
            "type": "message",
            "role": "assistant",
            "model": "kimi-k3",
            "content": [{"type": "text", "text": "2"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 256,
                "cache_creation": {ttl: 256},
            },
        }
        normalized = AnthropicConfig().transform_response(
            raw_response=httpx.Response(200, json=response),
            model_response=litellm.ModelResponse(),
            model="kimi-k3",
            messages=[],
            logging_obj=Mock(),
            optional_params={},
            api_key="",
            request_data={},
            encoding=litellm.encoding,
            json_mode=False,
            litellm_params={},
        )
        logging = Mock(
            model_call_details={"custom_llm_provider": "anthropic", "model": "kimi-k3"},
            litellm_params=kimi["litellm_params"],
        )
        logging.get_router_model_id.return_value = kimi["model_info"]["id"]
        now = datetime.now(timezone.utc)
        result = handler._create_anthropic_response_logging_payload(
            litellm_model_response=normalized,
            model="kimi-k3",
            kwargs={},
            start_time=now,
            end_time=now,
            logging_obj=logging,
        )
        cost = result["response_cost"]
        expected = (100 * 20 + 50 * 100 + 200 * 2 + 256 * write_rate) / 7.1 / 1_000_000
        assert isclose(cost, expected, rel_tol=0.00001), (ttl, cost, expected)
        assert logging.model_call_details["response_cost"] == cost
        start = copy.deepcopy(response)
        start.update(content=[], stop_reason=None)
        start["usage"]["output_tokens"] = 0
        events = [
            {"type": "message_start", "message": start},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "2"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 50},
            },
            {"type": "message_stop"},
        ]
        chunks = [
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        ]
        streamed = handler._build_complete_streaming_response(
            all_chunks=chunks,
            litellm_logging_obj=logging,
            model="kimi-k3",
        )
        result = handler._create_anthropic_response_logging_payload(
            litellm_model_response=streamed,
            model="kimi-k3",
            kwargs={},
            start_time=now,
            end_time=now,
            logging_obj=logging,
        )
        cost = result["response_cost"]
        assert isclose(cost, expected, rel_tol=0.00001), (ttl, cost, expected)
        assert logging.model_call_details["response_cost"] == cost
    # Claude Code compacts by the max_input_tokens /v1/models reports and asks
    # for 128,000 output tokens; DeepSeek refuses a request whose input plus
    # max_tokens exceeds 1,048,576. A conversation must be compacted before
    # it reaches the size DeepSeek refuses.
    # So the window /v1/models lists for a row is the one the row declares, not
    # LiteLLM's own 1,000,000 for the model.
    listed = create_model_info_response(
        model_id="deepseek-flash", provider="deepseek", llm_router=router
    )
    deepseek_input = listed.get("max_input_tokens")
    assert deepseek_input == DEEPSEEK_WINDOW, listed
    assert deepseek_input + 128_000 <= 1_048_576, listed
    print(
        "PASS: DeepSeek thinking, Kimi effort, and MiMo thinking verified; "
        f"deepseek-flash listed at {deepseek_input} input tokens; "
        "Kimi/MiMo image and tool history preserved; "
        "Kimi buffered/streamed cache costs verified; "
        "a blocked price-only deployment refused and fully priced"
    )


if __name__ == "__main__":
    main()
