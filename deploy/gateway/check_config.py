"""Run inside the pinned gateway image to check its real request transformation."""

import copy
from pathlib import Path
import sys

import litellm
from litellm.llms.anthropic.experimental_pass_through.messages.transformation import (
    AnthropicMessagesConfig,
)
import yaml


def main() -> None:
    config = yaml.safe_load(Path(sys.argv[1]).read_text())
    litellm.Router(model_list=config["model_list"])
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
    info = litellm.get_model_info("deepseek-flash", custom_llm_provider="deepseek")
    assert info["input_cost_per_token"] > 0, "Input tokens must remain billable"
    assert info["output_cost_per_token"] > 0, "Output tokens must remain billable"
    print("PASS: thinking/effort preserved; input and output pricing retained")


if __name__ == "__main__":
    main()
