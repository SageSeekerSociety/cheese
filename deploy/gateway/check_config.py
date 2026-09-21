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
    router = litellm.Router(model_list=config["model_list"])
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
    # Every model offered to people must be billable. A model whose tokens cost
    # zero is metered at zero, so the project's max_budget never trips and the
    # first sign of trouble is the invoice. Cheese drops such a model from the
    # catalogue rather than serving it with a dead brake, so the failure this
    # guards against is a model that quietly stops being offered — check it here,
    # where the price is written, instead of wondering there.
    offered = [
        item
        for item in router.model_list
        if item.get("model_info", {}).get("cheese_selectable") is True
    ]
    assert offered, "No model is marked cheese_selectable; agent settings would be bare"
    for deployment in offered:
        name = deployment["model_name"]
        # Router pricing is registered under the deployment id, including custom
        # models that do not appear in LiteLLM's built-in provider catalog.
        info = litellm.model_cost[deployment["model_info"]["id"]]
        assert info["input_cost_per_token"] > 0, f"{name}: input tokens must remain billable"
        assert info["output_cost_per_token"] > 0, f"{name}: output tokens must remain billable"
    print(
        "PASS: thinking/effort preserved; "
        f"{len(offered)} offered model(s) billable on both directions"
    )


if __name__ == "__main__":
    main()
