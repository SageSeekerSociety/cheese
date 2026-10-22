"""Recreate every model in the serving gateway's config.yaml as a runtime (DB) row.

The one-off step before the release that empties config.yaml's model list: run
it on each gateway while the color that is serving still has the old file, so
every model exists twice (config row and DB row) when the release removes the
config rows. Runs inside that color's LiteLLM container (the one the proxy has
ready; `deploy/release-gateway.sh` finds it the same way), reading its own
/app/config.yaml and calling its own admin API:

    docker exec -i <serving litellm container> python - > plan.jsonl < deploy/gateway/migrate_config_rows.py
    docker exec -i <serving litellm container> python - apply > apply.jsonl < deploy/gateway/migrate_config_rows.py

Without `apply` it prints the plan and writes nothing. With it, one JSON line
per row on stdout: keep that output, `verify_config_rows.py` reads it. After a
partial apply, pass the config indices already created (from that output) to
skip them: `apply skip=1,2,3`. /model/info hides a DB row's api_key, so this
script cannot tell on its own which rows exist already.

What a DB row needs that a config row does not:

- Prices go in litellm_params. A config row may write them under model_info
  (the official deepseek-flash row does, search_context_cost_per_query
  included), and LiteLLM 1.103.3 honours that for config rows only: a DB row
  with prices only under model_info is billed at LiteLLM's built-in defaults
  and loses its search price. Every pricing field found under model_info is
  copied into litellm_params (and left in model_info too).
- A price-only row (model_info.blocked) is blocked right after it is created:
  /model/new never writes the blocked column, only /model/block does, and a
  DB row's model_info.blocked is read from that column. For the second or so
  in between the row can route; it has no credential, and LiteLLM would fall
  back to ANTHROPIC_API_KEY for an `anthropic/` row without one. The gateway's
  compose file does not pass that variable, but if the container has it
  anyway, such a row is created with a key the upstream refuses
  (PRICE_ROW_KEY) instead.

`os.environ/...` references are kept verbatim; LiteLLM resolves them for DB rows
as it does for config rows.
"""

import json
import os
import sys
import urllib.request

import yaml
from litellm.types.utils import CustomPricingLiteLLMParams

BASE = "http://127.0.0.1:4000"
KEY = os.environ["LITELLM_MASTER_KEY"]
APPLY = len(sys.argv) > 1 and sys.argv[1] == "apply"
SKIP = {
    int(i)
    for arg in sys.argv[2:]
    if arg.startswith("skip=")
    for i in arg[5:].split(",")
    if i
}
# Written by LiteLLM itself, or meaningless on a DB row (blocked is a column).
DROP_INFO = {"id", "db_model", "blocked"}
# What a price-only row is created with when the container could otherwise lend
# it a real key from the environment for the moment before it is blocked.
PRICE_ROW_KEY = "price-row-no-credential"
LENDS_A_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
# The fields LiteLLM prices a deployment by, as it declares them.
PRICE_FIELDS = set(CustomPricingLiteLLMParams.model_fields) - {"output_vector_size"}


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def key_ref(value):
    if value in (None, PRICE_ROW_KEY) or (
        isinstance(value, str) and value.startswith("os.environ/")
    ):
        return value
    return "<literal>"


with open("/app/config.yaml") as f:
    config = yaml.safe_load(f)

for index, entry in enumerate(config.get("model_list") or [], 1):
    name = entry["model_name"]
    params = dict(entry["litellm_params"])
    info = {k: v for k, v in (entry.get("model_info") or {}).items() if k not in DROP_INFO}
    moved = sorted(k for k in info if k in PRICE_FIELDS and k not in params)
    for key in moved:
        params[key] = info[key]
    price_only = bool((entry.get("model_info") or {}).get("blocked"))
    if price_only and LENDS_A_KEY and "api_key" not in params:
        params["api_key"] = PRICE_ROW_KEY
    line = {
        "i": index,
        "model_name": name,
        "upstream": params.get("model"),
        # Only a reference is printed: the output is a log, and a literal key is not.
        "api_key_ref": key_ref(params.get("api_key")),
        "price_only": price_only,
        "prices_copied_from_model_info": moved,
    }
    if index in SKIP:
        print(json.dumps({**line, "action": "skip-done"}), flush=True)
        continue
    if not APPLY:
        print(json.dumps({**line, "action": "would-create", "info_keys": sorted(info)}), flush=True)
        continue
    body = {"model_name": name, "litellm_params": params}
    if info:
        body["model_info"] = info
    model_id = call("POST", "/model/new", body).get("model_id")
    line.update(action="created", model_id=model_id)
    if price_only:
        call("POST", "/model/block", {"model_id": model_id})
        line["blocked"] = True
    print(json.dumps(line), flush=True)
