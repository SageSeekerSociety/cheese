"""The 原理分解 figures' constants, read out of the code they describe.

`src/arch.mjs` draws two figures — one model request from a session process to
the vendor, one tool call out to a machine and back — station by station. Every
port, path, status code and limit those walks name is grepped here, out of the
file that enforces it, and build.mjs holds the result to `ARCH_SAMPLES`: the
page cannot say 8444 after the meter moved, and it cannot keep calling a binding
refusal a 429 after the proxy started answering 400.

Each fact carries the exact source text it came from, and build.mjs re-reads
that file to check the text is still in it — so a value that survives only
because it appears in a comment or another page is not enough.

The one thing this does NOT do is paraphrase prose. A refusal message is shown
on the page with placeholders (`<模型名>`), because the real one is an f-string
with `{requested!r}` in it; only the fields a client acts on — status, error
type, `reason_kind`, pool — are read verbatim.

Run by build.mjs through `gen()`, like gen/memory_limits.py: prints one JSON
object on stdout, never imports `app.*`.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

LLM_PROXY = "backend/app/api/routes/llm_proxy.py"
LLM_TUNNEL = "backend/app/api/routes/llm_tunnel.py"
CODEX_CHANNEL = "backend/app/domain/agent/harness/codex/channel.py"
PI_LAUNCH = "backend/app/domain/agent/harness/pi/launch.py"
ADDON = "deploy/metering-proxy/billing_addon.py"
COMPOSE = "deploy/metering-proxy/compose.yml"
CORE = "deploy/metering-proxy/cheese_billing_core.py"
CREDIT_SENTENCES = "frontend/src/i18n/messages/zh-CN/apiError.json"
MARKET = "backend/app/domain/agent/market.py"
CENTRAL = "backend/app/domain/agent/central_provider.py"
HEALTH = "backend/app/domain/device/health.py"
PLACE = "backend/app/domain/agent/place.py"
DISPATCH = "backend/app/domain/agent/dispatch_log.py"


class Missing(Exception):
    """A constant this script reads is no longer where (or what) it was."""


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def grab(path: str, pattern: str, group: int = 1) -> tuple[str, str]:
    """The first capture of `pattern` in `path`, plus the whole match as source."""
    match = re.search(pattern, text(path), re.S)
    if not match:
        raise Missing(f"{path}: nothing matches {pattern!r}")
    return match.group(group), match.group(0)


def load(name: str, path: str):
    """Import a pure module by path — no `app` package needed."""
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FACTS: dict[str, tuple] = {}


def fact(key: str, value, path, source) -> None:
    """One fact, and the exact source text it came from.

    A fact assembled from two greps («the router's prefix» + «the decorator on
    the route») carries one piece of source per grep: the two are not adjacent in
    the file, so no single string covers both. A fact read from two files (the
    two harnesses both pointing at `/llm/v1`) passes one path per piece.
    build.mjs checks every piece against the file it came from.
    """
    paths = [path] if isinstance(path, str) else list(path)
    pieces = [source] if isinstance(source, str) else list(source)
    if len(paths) == 1:
        paths = paths * len(pieces)
    if len(paths) != len(pieces):
        raise Missing(f"{key}: {len(paths)} files for {len(pieces)} pieces of source")
    FACTS[key] = {
        "value": value,
        "file": paths[0],
        "sources": [{"file": p, "text": t} for p, t in zip(paths, pieces)],
    }


def main() -> None:
    # ---------- the paths a machine or a session reaches us on ----------
    # The router prefixes and the route decorators, joined here so a prefix that
    # moves and a route that moves both land on the same dotted name.
    prefix, prefix_src = grab(LLM_PROXY, r'router = APIRouter\(prefix="([^"]+)"')
    admission, admission_src = grab(LLM_PROXY, r'@router\.post\("([^"]+)"')
    fact("paths.admission", prefix + admission, LLM_PROXY, [prefix_src, admission_src])
    tunnel_prefix, tunnel_prefix_src = grab(LLM_TUNNEL, r'router = APIRouter\(prefix="([^"]+)"')
    tunnel, tunnel_src = grab(LLM_TUNNEL, r'@router\.websocket\("([^"]+)"')
    fact("paths.tunnel", tunnel_prefix + tunnel, LLM_TUNNEL, [tunnel_prefix_src, tunnel_src])

    # The catch-all's public shape, read from BOTH harnesses that are pointed at
    # it: two launchers writing the same base URL is the fact, and one of them
    # drifting is exactly what this catches.
    codex, codex_src = grab(CODEX_CHANNEL, r"api \+ '([^']+)'")
    pi, pi_src = grab(PI_LAUNCH, r'\{api_base\}(/[^"]+)"')
    if codex != pi:
        raise Missing(f"{CODEX_CHANNEL} says {codex!r}, {PI_LAUNCH} says {pi!r}")
    fact("paths.catch_all", codex, [CODEX_CHANNEL, PI_LAUNCH], [codex_src, pi_src])

    # ---------- the meter's two listeners ----------
    # The port a session reaches is the PUBLISHED one; mitmproxy's own listen
    # port inside the container is a different number, and the mapping is the one
    # place that ties the two together.
    inside, inside_src = grab(COMPOSE, r'--mode reverse:https://api\.anthropic\.com@(\d+)')
    reverse, reverse_src = grab(COMPOSE, r'"172\.17\.0\.1:(\d+):' + inside)
    fact("ports.reverse", reverse, COMPOSE, [inside_src, reverse_src])
    # The regular listener is published on a variable host, but the same number
    # inside and out.
    connect, connect_src = grab(COMPOSE, r'--mode regular@(\d+)')
    connect_port, connect_port_src = grab(COMPOSE, r'\{CONNECT_BIND_HOST:-172\.17\.0\.1\}:(\d+):')
    if connect != connect_port:
        raise Missing(f"{COMPOSE}: the regular mode says {connect}, its published port says {connect_port}")
    fact("ports.connect", connect, COMPOSE, [connect_src, connect_port_src])

    # ---------- the two refusal shapes, and the one that is not a refusal ----------
    # 429 for a spent budget, 400 for a binding the catalogue cannot serve. The
    # kind is what the client acts on, so both are read, not just the number.
    budget_status, budget_src = grab(ADDON, r'_refuse\(flow, (\d+), "billing_error", message, headers\)')
    budget_type, _ = grab(ADDON, r'_refuse\(flow, \d+, "([^"]+)", message, headers\)')
    budget_prefix, prefix_src = grab(ADDON, r'_refuse_spent_budget\(\s*flow, f"([^"]+)\{verdict\.reason\}"')
    fact("budget.status", int(budget_status), ADDON, budget_src)
    fact("budget.type", budget_type, ADDON, budget_src)
    fact("budget.prefix", budget_prefix, ADDON, prefix_src)
    binding_status, binding_src = grab(ADDON, r'_refuse\(flow, (\d+), "invalid_request_error", verdict\.reason\)', 1)
    binding_type, _ = grab(ADDON, r'_refuse\(flow, \d+, "([^"]+)", verdict\.reason\)')
    fact("binding.status", int(binding_status), ADDON, binding_src)
    fact("binding.type", binding_type, ADDON, binding_src)
    # A CONNECT that cannot prove "bill this project" gets this instead of a
    # tunnel: the listener authenticates before it relays anything. It is the
    # one refusal made with a literal status — the others go through `_refuse`.
    refusal, refusal_src = grab(
        ADDON,
        r'http\.Response\.make\(\s*(\d+),\s*b"cheese: a valid scoped token is required as the proxy password"',
    )
    fact("connect_refusal", refusal, ADDON, refusal_src)

    # ---------- what a session carries, and what the meter replaces it with ----------
    placeholder, placeholder_src = grab(CORE, r'NO_LOGIN_PLACEHOLDER = "([^"]+)"')
    fact("placeholder_token", placeholder, CORE, placeholder_src)

    # ---------- the catalogue: one model, two names ----------
    # A subscription model is catalogued under a short id and sent upstream under
    # its full name; the page shows both, so both are read from the table that
    # knows they are the same model.
    table = re.search(r"_SUB_MODELS: list\[tuple\[[^\]]*\]\] = \[(.*?)\n\]", text(MARKET), re.S)
    if not table:
        raise Missing(f"{MARKET}: no _SUB_MODELS table")
    first = re.search(r'\(\s*"([^"]+)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]+)",', table.group(1), re.S)
    if not first:
        raise Missing(f"{MARKET}: the first _SUB_MODELS entry is not the shape this reads")
    fact("sub_model.id", first.group(1), MARKET, first.group(0))
    fact("sub_model.wire", first.group(4), MARKET, first.group(0))

    # ---------- the credit decision, read rather than described ----------
    # Admission answers with the ledger's own refusal sentence, so the page
    # shows the one a spent month really produces, not a paraphrase of it.
    allow, allow_src = grab(LLM_PROXY, r'"reason": "([^"]+)" if refused is None')
    fact("budget.allow_reason", allow, LLM_PROXY, allow_src)
    sentences = json.loads(text(CREDIT_SENTENCES))
    refuse = sentences["creditsMonthSpent"].format(month=11, day=1)
    fact("budget.refusal_reason", refuse, CREDIT_SENTENCES, '"creditsMonthSpent"')
    fail_open, fail_open_src = grab(CORE, r'Verdict\(True, "([^"]+)"\)\n\s*with self\._lock')
    fact("admission.fail_open_reason", fail_open, CORE, fail_open_src)

    # ---------- the machine side ----------
    probe, probe_src = grab(CENTRAL, r"MACHINE_PROBE_TIMEOUT_S = (\d+)")
    fact("probe_seconds", int(probe), CENTRAL, probe_src)
    threshold, threshold_src = grab(HEALTH, r"DEFAULT_FAILURE_THRESHOLD = (\d+)")
    fact("failure_threshold", int(threshold), HEALTH, threshold_src)
    cooldown, cooldown_src = grab(HEALTH, r"DEFAULT_QUARANTINE = timedelta\(minutes=(\d+)\)")
    fact("quarantine_minutes", int(cooldown), HEALTH, cooldown_src)
    root, root_src = grab(PLACE, r'_ROOT = "([^"]+)"')
    fact("footprint_root", root, PLACE, root_src)
    if not (ROOT / DISPATCH).exists():
        raise Missing(f"{DISPATCH}: the platform's own record of what it sent to a machine")
    fact("dispatch_log", Path(DISPATCH).name, DISPATCH, "def record(")

    json.dump({"facts": FACTS}, sys.stdout, ensure_ascii=False, indent=1)


try:
    main()
except Missing as exc:
    json.dump({"facts": {}, "error": str(exc)}, sys.stdout, ensure_ascii=False)
