#!/usr/bin/env python3
"""Is each model really the model it is billed as? -- CLI.

Run from ``backend/`` so the package on ``scripts/`` imports and so the
backend's own settings resolve the way the server's do:

    cd backend && uv run python scripts/probe_model_identity.py catalog
    cd backend && uv run python scripts/probe_model_identity.py enroll \\
        --models deepseek-flash,mimo-v2.6-pro,gpt-6-astra --preset quick
    cd backend && uv run python scripts/probe_model_identity.py verify \\
        --models deepseek-flash --reference-dir scripts/model_identity_probe/reference
    cd backend && uv run python scripts/probe_model_identity.py explain --seat <handle>

``catalog`` / ``enroll`` / ``verify`` speak to the platform's model gateway over
the same machine path a Codex or Pi harness uses: the seat's own scoped cheese
token as the Bearer on ``{backend}/llm/v1/...``. Nothing here reads, prints or
forwards a credential beyond the header it is supposed to ride in, and no probe
asks a model what it is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from scripts.model_identity_probe import battery, report  # noqa: E402
from scripts.model_identity_probe import reference as reference_mod
from scripts.model_identity_probe.collect import collect  # noqa: E402
from scripts.model_identity_probe.stats import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    DEFAULT_SAMPLES_PER_CELL,
)
from scripts.model_identity_probe.tokenizer_fp import (  # noqa: E402
    measure,
    to_reference,
)
from scripts.model_identity_probe.transport import Endpoint  # noqa: E402


def _backend() -> str:
    return os.environ.get("CHEESE_BACKEND", "http://172.17.0.1:8081")


def _token() -> str:
    token = os.environ.get("CHEESE_TOKEN", "")
    if not token:
        raise SystemExit(
            "CHEESE_TOKEN is not set; run this from a seat's own environment"
        )
    return token


def cmd_catalog(args: argparse.Namespace) -> int:
    with httpx.Client(timeout=60, trust_env=False) as client:
        response = client.get(
            f"{_backend().rstrip('/')}/llm/v1/models",
            headers={"authorization": f"Bearer {_token()}"},
        )
    if response.status_code != 200:
        print(
            f"gateway refused the catalog: HTTP {response.status_code} "
            f"{response.text[:200]}",
            file=sys.stderr,
        )
        return 1
    ids = sorted(
        {item.get("id") for item in response.json().get("data", []) if item.get("id")}
    )
    print(json.dumps({"models": ids, "count": len(ids)}, ensure_ascii=False, indent=2))
    return 0


def _endpoints(models: list[str], args: argparse.Namespace) -> list[Endpoint]:
    endpoints = []
    for model in models:
        if args.mode == "proxy":
            endpoints.append(
                Endpoint.proxy(model, proxy_url=args.proxy_url, ca_path=args.ca)
            )
        elif args.mode == "direct":
            endpoints.append(Endpoint.direct(model, args.base_url, args.api_key_env))
        else:
            endpoints.append(Endpoint.gateway(model, backend=args.backend))
    return endpoints


def cmd_enroll(args: argparse.Namespace) -> int:
    cells = battery.cells_for_preset(args.preset)
    out_dir = Path(args.reference_dir)
    written = []
    for endpoint in _endpoints(args.models, args):
        print(
            f"[enroll] {endpoint.model}: {len(cells)} cells x {args.samples} samples",
            file=sys.stderr,
        )
        reference = reference_mod.new_reference(
            endpoint.model,
            source=f"{endpoint.mode}:{endpoint.model}",
            samples_per_cell=args.samples,
            notes=(
                f"preset={args.preset} protocol={battery.SYSTEM_PROMPTS['en'][:24]}..."
            ),
        )
        reference.cells = collect(
            endpoint, cells, args.samples, args.concurrency, args.seed
        )
        sample = measure(endpoint)
        reference.tokenizer = to_reference(sample)
        target = reference_mod.save(reference, out_dir)
        valid = sum(s.valid for s in reference.cells.values())
        total = sum(s.total for s in reference.cells.values())
        written.append(
            {
                "model": endpoint.model,
                "path": str(target),
                "valid": valid,
                "total": total,
            }
        )
        print(
            f"[enroll] {endpoint.model}: {valid}/{total} valid, wrote {target}",
            file=sys.stderr,
        )
    print(json.dumps({"enrolled": written}, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    references = {}
    for model in args.models:
        loaded = reference_mod.load(model, Path(args.reference_dir))
        if loaded is None:
            print(
                f"[verify] no reference for {model}; behaviour will be insufficient",
                file=sys.stderr,
            )
        references[model] = loaded
    results = []
    for endpoint in _endpoints(args.models, args):
        result = report.verify(
            endpoint,
            endpoint.model,
            references.get(endpoint.model),
            samples_per_cell=args.samples,
            concurrency=args.concurrency,
            seed=args.seed,
        ).to_json()
        results.append(result)
        print(
            f"[verify] {result['model']}: {result['verdict']} -- {result['reason']}",
            file=sys.stderr,
        )
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if all(r["verdict"] != "mismatch" for r in results) else 2


def cmd_explain(args: argparse.Namespace) -> int:
    """Ask the control point itself which pool/model this seat resolves to.

    This is the cheap, deterministic half of the check, and it is the one that
    catches FB-73 directly: admission resolves the seat named in a credential
    and answers with the pool and the wire model, which is what the turn will
    actually run on. Cross-checking that answer against the card's route is
    what closes the seam.
    """
    headers = {"authorization": f"Bearer {_token()}"}
    if args.seat is not None and args.seat != "":
        headers["x-cheese-subagent"] = "1"
        headers["x-cheese-child-model"] = args.seat
    with httpx.Client(timeout=60, trust_env=False) as client:
        response = client.post(
            f"{_backend().rstrip('/')}/llm/admission", headers=headers, json={}
        )
    if response.status_code != 200:
        print(
            f"admission refused: HTTP {response.status_code} {response.text[:200]}",
            file=sys.stderr,
        )
        return 1
    data = response.json().get("data", {})
    print(
        json.dumps(
            {
                "allow": data.get("allow"),
                "supply": data.get("supply"),
                "reason": data.get("reason"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--models", default="", help="comma-separated catalog ids")
    common.add_argument(
        "--mode", choices=["gateway", "proxy", "direct"], default="gateway"
    )
    common.add_argument("--backend", default=_backend())
    common.add_argument("--proxy-url", default=os.environ.get("HTTPS_PROXY", ""))
    common.add_argument("--ca", default=os.environ.get("CHEESE_PROXY_CA", ""))
    common.add_argument("--base-url", default="")
    common.add_argument("--api-key-env", default="")
    common.add_argument(
        "--preset", choices=sorted(battery.PROBE_PRESETS), default="quick"
    )
    common.add_argument("--samples", type=int, default=DEFAULT_SAMPLES_PER_CELL)
    common.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    common.add_argument("--seed", type=int, default=20261005)
    common.add_argument("--reference-dir", default=str(reference_mod.REFERENCE_DIR))

    sub.add_parser("catalog", help="list the models the gateway serves")
    sub.add_parser("enroll", parents=[common], help="build reference fingerprints")
    sub.add_parser(
        "verify", parents=[common], help="verify endpoints against references"
    )
    explain = sub.add_parser(
        "explain", help="ask admission which pool/model a seat resolves to"
    )
    explain.add_argument(
        "--seat", default="", help="catalog model id; omit for the seat's own binding"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in ("enroll", "verify"):
        args.models = [m.strip() for m in args.models.split(",") if m.strip()]
        if not args.models:
            raise SystemExit("--models is required")
    return {
        "catalog": cmd_catalog,
        "enroll": cmd_enroll,
        "verify": cmd_verify,
        "explain": cmd_explain,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
