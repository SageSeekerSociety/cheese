"""Ask the gateway, one model at a time, whether that supply can still serve.

"The AI doesn't answer" has two very different causes — a broken route and an
empty account — and they are indistinguishable from the chat UI. This sends the
smallest possible real completion to every model the gateway lists and prints
the upstream's own words, so the answer is the provider's, not an inference.

Run inside the gateway container (it holds the master key):

  docker exec cheese-gateway-litellm-1 python /tmp/gateway_supply_probe.py
"""

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("GATEWAY_BASE", "http://127.0.0.1:4000")
KEY = os.environ.get("LITELLM_MASTER_KEY", "")


def _post(path: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400]
    except Exception as exc:  # noqa: BLE001 - the point is to report, not to raise
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    req = urllib.request.Request(
        f"{BASE}/v1/models", headers={"Authorization": f"Bearer {KEY}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        models = [m["id"] for m in json.load(resp)["data"]]
    print("models:", models, flush=True)

    for model in models:
        status, body = _post(
            "/v1/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
            },
        )
        verdict = "SERVES" if status == 200 else "REFUSED"
        print(f"\n--- {model}: {verdict} (HTTP {status})", flush=True)
        print(f"    {body}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
