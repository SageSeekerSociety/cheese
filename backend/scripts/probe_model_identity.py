#!/usr/bin/env python3
"""Is each model really the model it is billed as? -- CLI.

Run from ``backend/`` so the package on ``scripts/`` imports and so the
backend's own settings resolve the way the server's do. The default mode is
``seat``: a probe goes over the seat's OWN road (the metering proxy's CONNECT
listener), which is the road a real turn takes.

    cd backend && uv run python scripts/probe_model_identity.py catalog
    cd backend && uv run python scripts/probe_model_identity.py enroll \\
        --models deepseek-flash,mimo-v2.6-pro,gpt-6-astra --preset quick
    cd backend && uv run python scripts/probe_model_identity.py verify \\
        --expected-model claude-opus-5-5 --expected-pool subscription
    cd backend && uv run python scripts/probe_model_identity.py explain

``verify`` on a seat needs no ``--models``: it takes the model and pool from an
independent declaration, in this order --

  1. ``--expected-model`` / ``--expected-pool`` on the command line;
  2. the seat's own saved config, ``GET /topics/{topic}/agent/control``
     (``state.init.model``), which a room credential may read;
  3. (last, and today usually empty) the room's own turn record.

``/llm/admission`` is read too, but only as a third party: it is the SAME call
the metering proxy makes to rewrite the request body and pick the pool, so an
expectation taken from it would make ``expected == wire`` by construction and
the FB-73 false response would read as a match. Admission disagreeing with the
independent declaration is a mismatch, not the declaration.

``catalog`` / ``enroll`` / ``verify`` in ``seat`` mode go over the seat's own
CONNECT road (``HTTPS_PROXY``, ``CHEESE_CONNECT_TOKEN`` or the tunnel's token
file); ``--mode gateway`` samples the gateway pool directly with the seat's
scoped cheese token as the Bearer on ``{backend}/llm/v1/...``. Nothing here
reads, prints or forwards a credential beyond the header it is supposed to ride
in, and no probe asks a model what it is.
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
from scripts.model_identity_probe import binding as binding_mod  # noqa: E402
from scripts.model_identity_probe import reference as reference_mod
from scripts.model_identity_probe.binding import (  # noqa: E402
    DeclaredBinding,
    check_seat,
)
from scripts.model_identity_probe.collect import collect  # noqa: E402
from scripts.model_identity_probe.stats import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    DEFAULT_SAMPLES_PER_CELL,
    PROBE_PROTOCOL,
    PROBE_TEMPERATURE,
)
from scripts.model_identity_probe.tokenizer_fp import (  # noqa: E402
    measure,
    to_reference,
)
from scripts.model_identity_probe.transport import (  # noqa: E402
    Endpoint,
    detect_adapter,
)


def _backend() -> str:
    # CHEESE_API is what the platform injects for the harness's own calls and is
    # the only base the platform ever supplies. CHEESE_BACKEND is not a variable
    # the platform sets, so reading it only ever dialled a box-local address that
    # exists on the backend's host and nowhere else.
    return os.environ.get("CHEESE_API") or "http://172.17.0.1:8081"


def _token() -> str:
    token = os.environ.get("CHEESE_TOKEN", "")
    if not token:
        raise SystemExit(
            "CHEESE_TOKEN is not set; run this from a seat's own environment"
        )
    return token


def read_binding(args: argparse.Namespace) -> dict:
    """What ``/llm/admission`` says this credential resolves to -- a third party.

    Admission resolves the seat named in the credential and answers with the
    pool and the wire model the turn will run on (``supply``). It is NOT the
    expectation (R3-1): the metering proxy asks the same endpoint to rewrite the
    request body and pick the pool, so ``expected == wire`` from this answer
    would be a tautology, and FB-73's false response would read as a match. Here
    it is read so it can be cross-checked against the independent declaration.

    ``--child-model``, when set, asks admission to resolve a subagent's model
    (the ``x-cheese-subagent`` path); the seat's own binding is asked otherwise.
    """
    # Ask with the CONNECT credential when it is readable: that is the token the
    # metering proxy presents to admission for a real turn, and FB-73 is that
    # token naming the wrong seat. CHEESE_TOKEN is the fallback, said as such.
    connect = binding_mod.connect_credential()
    headers = {"authorization": f"Bearer {connect or _token()}"}
    child_model = getattr(args, "child_model", "") or ""
    if child_model:
        headers["x-cheese-subagent"] = "1"
        headers["x-cheese-child-model"] = child_model
    try:
        with httpx.Client(timeout=60, trust_env=False) as client:
            response = client.post(
                f"{_backend().rstrip('/')}/llm/admission", headers=headers, json={}
            )
    except httpx.HTTPError as exc:
        # A third party that cannot be read is not fatal: the verdict just
        # loses this cross-check (and says so), like an unreachable wire.
        return {"error": f"admission unreachable: {type(exc).__name__}"}
    if response.status_code != 200:
        return {"error": f"admission refused: HTTP {response.status_code}"}
    data = response.json().get("data", {})
    supply = data.get("supply") or {}
    return {
        "allow": data.get("allow"),
        "model": supply.get("model"),
        "pool": supply.get("pool"),
        "reason": data.get("reason"),
        "credential": "connect" if connect else "CHEESE_TOKEN",
    }


def _room_topic(args: argparse.Namespace) -> str:
    return (getattr(args, "topic", "") or os.environ.get("CHEESE_TOPIC", "")).strip()


def read_seat_config_binding(args: argparse.Namespace) -> DeclaredBinding:
    """The model the seat's own session says it was launched with.

    ``GET /topics/{topic}/agent/control`` answers with the session's
    ``state.init.model`` -- the "card" side of the binding, and the one a room
    credential can read (the project-agents route is 403 for it). No pool rides
    this payload. A failure here is not fatal: it just leaves this source empty
    and the caller leans on the operator's declaration.
    """
    topic = _room_topic(args)
    if not topic:
        return DeclaredBinding()
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.get(
                f"{_backend().rstrip('/')}/topics/{topic}/agent/control",
                # No ``agent`` parameter: the route keys seats by the agent
                # TYPE handle (e.g. ``cheesex-opus-cc``), not the room handle
                # ``--seat`` carries. In a room with several seats it answers
                # an empty state, and this source is then simply empty.
                headers={"X-Cheese-Token": _token()},
            )
    except httpx.HTTPError:
        return DeclaredBinding()
    if response.status_code != 200:
        return DeclaredBinding()
    return binding_mod.declared_from_seat_config(response.json().get("data") or {})


def read_card_binding(args: argparse.Namespace) -> DeclaredBinding:
    """The model a room's own turn record declares, if any.

    ``GET /topics/{topic}/blocks`` is readable by a room credential; today the
    block payload carries no model, so this is usually empty (the room records a
    turn's *route*, not its model). Read best-effort, and never fatal.
    """
    topic = _room_topic(args)
    if not topic:
        return DeclaredBinding()
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.get(
                f"{_backend().rstrip('/')}/topics/{topic}/blocks",
                params={"limit": 50},
                headers={"X-Cheese-Token": _token()},
            )
    except httpx.HTTPError:
        return DeclaredBinding()
    if response.status_code != 200:
        return DeclaredBinding()
    body = response.json().get("data") or {}
    blocks = body.get("data") if isinstance(body, dict) else None
    return binding_mod.declared_from_card(blocks or [])


def read_declared_binding(args: argparse.Namespace) -> DeclaredBinding:
    """The expected binding, from independent sources -- never from admission.

    Precedence, most trusted first: the operator's ``--expected-model`` /
    ``--expected-pool``; the seat's own saved config (``agent/control``); the
    room's turn record/card. Each fills only the fields the ones before it left
    empty.
    """
    declared = binding_mod.declared_from_operator(
        getattr(args, "expected_model", ""), getattr(args, "expected_pool", "")
    )
    if not (declared.model and declared.pool):
        declared = declared.merged(read_seat_config_binding(args))
    if not (declared.model and declared.pool):
        declared = declared.merged(read_card_binding(args))
    return declared


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
        if args.mode == "seat":
            endpoints.append(
                Endpoint.seat(
                    model,
                    proxy_url=args.proxy_url,
                    connect_host=args.connect_host,
                    ca_path=args.ca,
                )
            )
        elif args.mode == "proxy":
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
        # Discover the reasoning-disable field once; both the behaviour samples
        # and the tokenizer probes must use it, and its name goes in the notes
        # so a later reader can compare two fingerprints' conditions.
        adapter = detect_adapter(endpoint, next(iter(cells)))
        temperature_note = (
            "omitted" if adapter.omit_temperature else str(PROBE_TEMPERATURE)
        )
        reference = reference_mod.new_reference(
            endpoint.model,
            source=f"{endpoint.mode}:{endpoint.model}",
            samples_per_cell=args.samples,
            notes=(
                f"protocol={PROBE_PROTOCOL} preset={args.preset} "
                f"temperature={temperature_note} max_tokens={adapter.max_tokens} "
                f"reasoning={adapter.strategy}"
                + (" post_reasoning" if adapter.post_reasoning else "")
            ),
        )
        reference.cells = collect(
            endpoint,
            cells,
            args.samples,
            args.concurrency,
            args.seed,
            adapter=adapter,
        )
        sample = measure(endpoint, adapter=adapter)
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
    # The expectation comes from an INDEPENDENT source, never from admission
    # (R3-1): admission is the same call the metering proxy makes to rewrite the
    # request body and pick the pool, so taking it as the expectation makes
    # expected == wire by construction and FB-73's false response a match.
    declared = read_declared_binding(args)
    expected_pool = declared.pool
    expected_model = declared.model
    if not expected_model and args.models:
        # No independent model declared: the operator's --models IS the claim.
        expected_model = args.models[0]
        if declared.source == "none":
            declared = DeclaredBinding(
                model=expected_model, pool=None, source="operator"
            )
    if not args.models and expected_model:
        # No --models given: verify the model the declaration names.
        args.models = [expected_model]
    if args.mode == "seat" and not args.models:
        raise SystemExit(
            "seat mode needs a model: pass --models or --expected-model, or a "
            "seat whose saved config names one"
        )

    # The seat check: the credential's own seat claim vs the seat this session
    # is. FB-73's root cause is exactly this disagreement (R3-1/2), and it is a
    # mismatch on its own, independent of any wire or admission answer.
    seat = check_seat(
        _token(),
        getattr(args, "seat", ""),
        None,
        connect_token=binding_mod.connect_credential(),
    )

    # Admission, read only as a third party (see read_binding). Its disagreement
    # with the declaration is a mismatch in `combine`.
    admission = None
    if args.mode == "seat":
        reading = read_binding(args)
        if "error" in reading:
            print(
                f"[verify] {reading['error']}; no admission cross-check",
                file=sys.stderr,
            )
        else:
            admission = report.AdmissionReading.from_mapping(reading)

    references = {}
    for model in args.models:
        loaded = reference_mod.load(model, Path(args.reference_dir))
        if loaded is None:
            print(
                f"[verify] no reference for {model}; verdict rests on the "
                "deterministic wire signals only",
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
            expected_pool=expected_pool,
            expected_model=expected_model or endpoint.model,
            admission=admission,
            seat=seat,
        ).to_json()
        results.append(result)
        print(
            f"[verify] {result['model']}: {result['verdict']} -- {result['reason']}",
            file=sys.stderr,
        )
    payload = {
        "declaration": {
            "expected_model": expected_model,
            "expected_pool": expected_pool,
            "source": declared.source,
        },
        "seat": (
            None
            if seat is None
            else {
                "ok": seat.ok,
                "credential_seat": seat.credential_seat,
                "session_seat": seat.session_seat,
                "reason": seat.reason,
            }
        ),
        "admission": (
            None
            if admission is None
            else {
                "model": admission.model,
                "pool": admission.pool,
                "allow": admission.allow,
            }
        ),
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(r["verdict"] != "mismatch" for r in results) else 2


def cmd_explain(args: argparse.Namespace) -> int:
    """Print what the platform declares for this credential's seat.

    This is only the *declaration*: admission resolves the seat named in the
    credential and answers with the pool and the wire model it will run. It is
    NOT a verification -- the declaration is exactly what can be wrong (FB-73),
    and admission reads the same credential that may have been mis-issued. To
    check the declaration against the wire, use ``verify --mode seat``, which
    samples over the seat's own CONNECT road and compares the response against
    this answer.
    """
    binding = read_binding(args)
    print(json.dumps(binding, ensure_ascii=False, indent=2))
    return 0 if binding.get("allow") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--models", default="", help="comma-separated catalog ids")
    common.add_argument(
        "--mode",
        choices=["seat", "gateway", "proxy", "direct"],
        default="seat",
        help=(
            "seat (default) samples over the seat's own CONNECT road, where the "
            "metering proxy writes the binding's model into the body -- the road "
            "a real turn takes; gateway samples the gateway pool directly"
        ),
    )
    common.add_argument("--backend", default=_backend())
    common.add_argument("--proxy-url", default=os.environ.get("HTTPS_PROXY", ""))
    common.add_argument("--connect-host", default="")
    common.add_argument("--ca", default=os.environ.get("CHEESE_PROXY_CA", ""))
    common.add_argument("--base-url", default="")
    common.add_argument("--api-key-env", default="")
    common.add_argument(
        "--expected-pool",
        default="",
        choices=["", "subscription", "gateway"],
        help=(
            "the pool this seat is declared to run on (highest-trust source; "
            "defaults to the seat's saved config)"
        ),
    )
    common.add_argument(
        "--expected-model",
        default="",
        help=(
            "the model this seat is declared to run on (highest-trust source; "
            "defaults to the seat's saved config). Enters the verdict."
        ),
    )
    common.add_argument(
        "--seat",
        default="",
        help=(
            "the handle this turn was addressed to (the seat the agent posts "
            "as). The only seat declaration not written by the session's own "
            "launch: without it a verdict is capped at uncertain, because "
            "FB-73's relaunch wrote the wrong seat into every other one."
        ),
    )
    common.add_argument(
        "--child-model",
        default="",
        help=(
            "ask admission to resolve a subagent's model instead of the seat's "
            "own binding (the x-cheese-subagent path)"
        ),
    )
    common.add_argument(
        "--topic",
        default="",
        help=(
            "the conversation whose seat config/record to read for the expected "
            "binding; defaults to CHEESE_TOPIC"
        ),
    )
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
    sub.add_parser(
        "explain",
        parents=[common],
        help="ask admission which pool/model a seat resolves to",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in ("enroll", "verify"):
        args.models = [m.strip() for m in args.models.split(",") if m.strip()]
        # Seat mode may leave --models empty and take the model from the
        # binding admission resolves; every other mode needs it named.
        if not args.models and getattr(args, "mode", "") != "seat":
            raise SystemExit("--models is required")
    return {
        "catalog": cmd_catalog,
        "enroll": cmd_enroll,
        "verify": cmd_verify,
        "explain": cmd_explain,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
