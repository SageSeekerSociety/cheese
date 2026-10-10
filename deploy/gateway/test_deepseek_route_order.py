"""deepseek-flash answers from RUC while RUC answers, and from DeepSeek otherwise."""

import asyncio
import copy
import os
from pathlib import Path

import litellm
import yaml

RUC = "http://ruc.invalid:4000"
# What RUC answers when it is past its parallel limit, and when it is failing.
FAILURES = {"busy": "litellm.RateLimitError", "failing": "litellm.InternalServerError"}


def deployments(ruc_answer):
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    out = []
    for entry in copy.deepcopy(config["model_list"]):
        if entry["model_name"] != "deepseek-flash":
            continue
        params = entry["litellm_params"]
        on_ruc = params.get("api_base") == "os.environ/RUC_DEEPSEEK_API_BASE"
        params["mock_response"] = ruc_answer if on_ruc else "official"
        out.append(entry)
    return out


async def answer(router):
    response = await router.acompletion(
        model="deepseek-flash", messages=[{"role": "user", "content": "hi"}]
    )
    return response.choices[0].message.content


async def main():
    os.environ["RUC_DEEPSEEK_API_BASE"] = RUC
    os.environ["RUC_DEEPSEEK_API_KEY"] = "ruc-key"
    os.environ.setdefault("DEEPSEEK_API_KEY", "official-key")

    healthy = litellm.Router(model_list=deployments("ruc"))
    assert len(healthy.get_model_list(model_name="deepseek-flash")) == 2
    for _ in range(5):
        assert await answer(healthy) == "ruc"

    for kind, error in FAILURES.items():
        router = litellm.Router(model_list=deployments(error))
        assert await answer(router) == "official", kind

    print("PASS: deepseek-flash served by RUC first; busy and failing RUC fall over to DeepSeek")


if __name__ == "__main__":
    asyncio.run(main())
