"""Check each DB row migrate_config_rows.py created against its config.yaml twin.

Runs inside the same serving LiteLLM container as the migration (the color the
proxy has ready), while its /app/config.yaml still lists the models. `python -`
reads the script from stdin, so the apply output goes in as one base64 argument:

    docker exec -i <serving litellm container> python - "$(base64 < apply.jsonl | tr -d '\\n')" < deploy/gateway/verify_config_rows.py

For every created row it compares, against what /model/info reports for the DB
row: the model name and upstream, every price key (read from litellm_params,
else model_info, on either side), every model_info key the config row declares,
and that price-only rows report blocked. One line per row; exits 1 on any
mismatch.

One difference is not a mismatch: a DB row reports cache_creation_input_token_cost
as 0.0 where the config row sets none. Neither bills a cache write.
"""

import base64
import json
import os
import sys
import urllib.request

import yaml

PRICE_KEYS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
    "cache_creation_input_token_cost_above_1hr",
)
# Unset in config.yaml and 0.0 on the DB row: the same thing, nothing billed.
ZERO_WHEN_UNSET = {"cache_creation_input_token_cost"}


def info_rows():
    req = urllib.request.Request(
        "http://127.0.0.1:4000/model/info",
        headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"]},
    )
    data = json.load(urllib.request.urlopen(req, timeout=60))["data"]
    return {r["model_info"]["id"]: r for r in data}


def price(params, info, key):
    # A config row may put a price under model_info (the deepseek official row does).
    value = params.get(key, info.get(key))
    return None if value is None else float(value)


def same_price(key, want, got):
    if want == got:
        return True
    return key in ZERO_WHEN_UNSET and want is None and got == 0.0


log = [
    json.loads(line)
    for line in base64.b64decode(sys.argv[1]).decode().splitlines()
    if line.strip()
]
config = yaml.safe_load(open("/app/config.yaml"))["model_list"]
rows = info_rows()
bad = 0
for entry in log:
    if entry.get("action") != "created":
        continue
    want = config[entry["i"] - 1]
    got = rows.get(entry["model_id"])
    problems = []
    if got is None:
        problems.append("not in /model/info")
    else:
        wp, wi = want["litellm_params"], want.get("model_info") or {}
        gp, gi = got.get("litellm_params") or {}, got.get("model_info") or {}
        if not gi.get("db_model"):
            problems.append("not db_model")
        if got["model_name"] != want["model_name"] or gp.get("model") != wp.get("model"):
            problems.append("name/upstream differs")
        for key in PRICE_KEYS:
            w, g = price(wp, wi, key), price(gp, gi, key)
            if not same_price(key, w, g):
                problems.append(f"{key} {w} != {g}")
        for key, value in wi.items():
            if key in ("id", "db_model", "blocked") or key in PRICE_KEYS:
                continue
            if gi.get(key) != value:
                problems.append(f"model_info.{key} {value!r} != {gi.get(key)!r}")
        if bool(wi.get("blocked")) != bool(gi.get("blocked")):
            problems.append(f"blocked {bool(wi.get('blocked'))} != {bool(gi.get('blocked'))}")
    bad += bool(problems)
    status = "OK" if not problems else "MISMATCH: " + "; ".join(problems)
    print(entry["i"], entry["model_name"], status)
print(f"checked={sum(e.get('action') == 'created' for e in log)} mismatched={bad}")
sys.exit(1 if bad else 0)
