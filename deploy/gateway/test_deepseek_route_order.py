"""The configured DeepSeek deployment uses the official API's checkpoint."""

import asyncio
import copy
import os
from pathlib import Path

import litellm
import yaml


def deployments(*, official_fails=False):
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    out = []
    for entry in copy.deepcopy(config["model_list"]):
        if entry["model_name"] != "deepseek-flash":
            continue
        params = entry["litellm_params"]
        official = params["model"] == "deepseek/deepseek-flash" and not params.get(
            "api_base"
        )
        params["mock_response"] = (
            "litellm.InternalServerError"
            if official_fails and official
            else "official"
            if official
            else "unverified checkpoint"
        )
        out.append(entry)
    return out


async def answer(router):
    response = await router.acompletion(
        model="deepseek-flash", messages=[{"role": "user", "content": "hi"}]
    )
    return response.choices[0].message.content


async def main():
    os.environ.setdefault("DEEPSEEK_API_KEY", "official-key")
    os.environ["RUC_DEEPSEEK_API_BASE"] = "http://ruc.invalid:4000"
    for n in range(1, 10):
        os.environ[f"RUC_DEEPSEEK_API_KEY_{n}"] = f"ruc-key-{n}"

    healthy = litellm.Router(model_list=deployments(), num_retries=0)
    for _ in range(3):
        assert await answer(healthy) == "official"
    try:
        await answer(
            litellm.Router(model_list=deployments(official_fails=True), num_retries=0)
        )
    except litellm.InternalServerError:
        pass
    else:
        raise AssertionError(
            "An official API failure must not select an unverified checkpoint"
        )
    print(
        "PASS: DeepSeek uses the official API; failure never selects an unverified checkpoint"
    )


if __name__ == "__main__":
    asyncio.run(main())
