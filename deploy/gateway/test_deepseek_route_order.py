"""deepseek-flash answers from RUC while any RUC key answers, and from DeepSeek otherwise."""

import asyncio
import copy
import os
from pathlib import Path

import litellm
import yaml

RUC_BASE = "os.environ/RUC_DEEPSEEK_API_BASE"
# What a RUC key answers when it is past its parallel limit, and when it is failing.
FAILURES = {"busy": "litellm.RateLimitError", "failing": "litellm.InternalServerError"}


def deployments(answer_for_key):
    """deepseek-flash's rows, each answering by mock: RUC rows by their key."""
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    out = []
    for entry in copy.deepcopy(config["model_list"]):
        if entry["model_name"] != "deepseek-flash":
            continue
        params = entry["litellm_params"]
        if params.get("api_base") == RUC_BASE:
            params["mock_response"] = answer_for_key(params["api_key"])
        else:
            params["mock_response"] = "official"
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

    healthy = litellm.Router(model_list=deployments(lambda key: "ruc"))
    rows = healthy.get_model_list(model_name="deepseek-flash")
    ruc_keys = [
        r["litellm_params"]["api_key"]
        for r in rows
        if r["litellm_params"].get("api_base") == os.environ["RUC_DEEPSEEK_API_BASE"]
    ]
    assert len(ruc_keys) == 9 and len(set(ruc_keys)) == 9, ruc_keys
    assert len({r["model_info"]["id"] for r in rows}) == len(rows) == 10, rows
    for _ in range(10):
        assert await answer(healthy) == "ruc"

    # One key past its limit: the request stays on RUC, on another key.
    for _ in range(10):
        one_busy = litellm.Router(
            model_list=deployments(
                lambda key: FAILURES["busy"]
                if key == "os.environ/RUC_DEEPSEEK_API_KEY_1"
                else "ruc"
            )
        )
        assert await answer(one_busy) == "ruc"

    for kind, error in FAILURES.items():
        router = litellm.Router(model_list=deployments(lambda key: error))
        assert await answer(router) == "official", kind

    print(
        "PASS: deepseek-flash served by RUC across 9 keys; one busy key stays on RUC; "
        "every key busy or failing falls over to DeepSeek"
    )


if __name__ == "__main__":
    asyncio.run(main())
