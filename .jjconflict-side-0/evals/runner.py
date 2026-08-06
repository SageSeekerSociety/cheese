"""CheeseX agent eval runner — 回归基建：改 prompt/换模型不许退化 (docs/evals.md).

Boots an ISOLATED backend (fresh sqlite + own workspace root, port 8097), drives
each scenario through the real API with real agent turns (the same gateway the
dev backend uses), then has a judge model score the evidence against the
scenario's rubric. Deterministic platform checks and the judge verdict combine
into PASS / PARTIAL / FAIL; a failing scenario never aborts the run.

Usage (from the repo root):
    uv run --project backend python evals/runner.py --list
    uv run --project backend python evals/runner.py --scenario C1
    uv run --project backend python evals/runner.py --scenario C1 --scenario C3
    uv run --project backend python evals/runner.py --all

Outputs: evals/results/<timestamp>/report.md + results.jsonl (+ runner.log,
backend.log, eval.db) — full inputs/outputs per scenario, replayable.
"""

import argparse
import asyncio
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import dotenv_values  # noqa: E402 (needs sys.path first)

from evals.lib.backend import DEFAULT_PORT, EvalBackend  # noqa: E402
from evals.lib.client import EvalApi  # noqa: E402
from evals.lib.context import EvalContext  # noqa: E402
from evals.lib.judge import JudgeConfig, run_judge  # noqa: E402
from evals.lib.report import append_jsonl, now_iso, write_report  # noqa: E402
from evals.lib.records import Scenario, ScenarioResult  # noqa: E402
from evals.scenarios import discover  # noqa: E402

RESULTS_ROOT = Path(__file__).resolve().parent / "results"
BACKEND_ENV = dotenv_values(REPO_ROOT / "backend" / ".env")


def _setup_logging(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger("evals")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z"
    )
    for handler in (
        logging.FileHandler(run_dir / "runner.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


def _judge_config(args: argparse.Namespace) -> JudgeConfig:
    """Judge model/gateway: CLI flag > EVAL_JUDGE_* env > backend/.env values."""
    import os

    base_url = (
        os.environ.get("EVAL_JUDGE_BASE_URL")
        or BACKEND_ENV.get("ANTHROPIC_BASE_URL")
        or ""
    )
    api_key = (
        os.environ.get("EVAL_JUDGE_API_KEY")
        or BACKEND_ENV.get("ANTHROPIC_AUTH_TOKEN")
        or ""
    )
    model = (
        args.judge_model
        or os.environ.get("EVAL_JUDGE_MODEL")
        or BACKEND_ENV.get("AGENT_MODEL")
        or "glm-5.2"
    )
    if not base_url or not api_key:
        raise SystemExit(
            "judge gateway not configured: need ANTHROPIC_BASE_URL / "
            "ANTHROPIC_AUTH_TOKEN in backend/.env (or EVAL_JUDGE_* env vars)"
        )
    return JudgeConfig(base_url=base_url, api_key=api_key, model=model)


def _verdict(result: ScenarioResult) -> str:
    if result.error is not None:
        return "FAIL"
    if any(not c.passed for c in result.checks):
        return "FAIL"
    if result.judge is None:
        return "PASS"
    return {2: "PASS", 1: "PARTIAL"}.get(result.judge.score, "FAIL")


async def _run_scenario(
    scenario: Scenario,
    ctx: EvalContext,
    judge_config: JudgeConfig,
    log: logging.Logger,
) -> ScenarioResult:
    result = ScenarioResult(
        scenario_id=scenario.id,
        name=scenario.name,
        verdict="FAIL",
        started_at=now_iso(),
        duration_s=0.0,
    )
    t0 = time.monotonic()
    log.info("=== %s %s: start (timeout %ss)", scenario.id, scenario.name,
             scenario.timeout_s)
    try:
        async with asyncio.timeout(scenario.timeout_s):
            outcome = await scenario.run(ctx)
        result.inputs = outcome.inputs
        result.evidence = outcome.evidence
        result.checks = outcome.checks
        for c in outcome.checks:
            log.info("%s check %-28s %s  %s", scenario.id, c.name,
                     "PASS" if c.passed else "FAIL", c.detail)
        if scenario.rubric and outcome.judge_evidence:
            log.info("%s judging with %s ...", scenario.id, judge_config.model)
            result.judge = await run_judge(
                rubric=scenario.rubric,
                evidence=outcome.judge_evidence,
                config=judge_config,
            )
            log.info("%s judge: score=%d reason=%s", scenario.id,
                     result.judge.score, result.judge.reason)
    except TimeoutError:
        result.error = f"scenario timed out after {scenario.timeout_s}s"
        log.error("%s TIMEOUT: %s", scenario.id, result.error)
    except Exception as exc:  # noqa: BLE001 — one scenario must not kill the run
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("%s FAILED with exception", scenario.id)
    result.duration_s = round(time.monotonic() - t0, 1)
    result.verdict = _verdict(result)
    log.info("=== %s %s: %s (%.0fs)", scenario.id, scenario.name,
             result.verdict, result.duration_s)
    return result


async def _amain(args: argparse.Namespace) -> int:
    registry = discover()
    if args.list:
        for s in registry.values():
            judge = "judge" if s.rubric else "deterministic"
            print(f"{s.id:4s} {s.name} — {s.description} [{judge}]")
        return 0

    wanted = args.scenario or list(registry)
    unknown = [s for s in wanted if s not in registry]
    if unknown:
        raise SystemExit(
            f"unknown scenario(s): {unknown} — available: {list(registry)}"
        )
    scenarios = [registry[s] for s in wanted]

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    run_dir = RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    log = _setup_logging(run_dir)
    judge_config = _judge_config(args)

    run_meta = {
        "run_id": run_id,
        "started_at": now_iso(),
        "backend": f"127.0.0.1:{args.port} (isolated, sqlite)",
        "agent_model": BACKEND_ENV.get("AGENT_MODEL", "glm-5.2"),
        "gateway": BACKEND_ENV.get("ANTHROPIC_BASE_URL", "(anthropic default)"),
        "sandbox": "off (plain model turns, no platform tools)",
        "judge_model": judge_config.model,
        "scenarios": [s.id for s in scenarios],
    }
    log.info("run %s: scenarios=%s results=%s", run_id, wanted, run_dir)

    backend = EvalBackend(run_dir, port=args.port)
    log.info("starting isolated backend on port %d ...", args.port)
    backend.start()
    log.info("backend healthy at %s (db=%s)", backend.base_url, backend.db_path)

    api = EvalApi(
        backend.base_url, backend.ws_base_url, sandbox_token=backend.sandbox_token
    )
    ctx = EvalContext(api=api, log=log)
    results: list[ScenarioResult] = []
    try:
        for scenario in scenarios:
            result = await _run_scenario(scenario, ctx, judge_config, log)
            results.append(result)
            append_jsonl(run_dir / "results.jsonl", result, run_meta)
    finally:
        await api.close()
        backend.stop()
        log.info("backend stopped")

    write_report(run_dir / "report.md", results, run_meta)
    log.info("report: %s", run_dir / "report.md")
    for r in results:
        log.info("RESULT %s %s -> %s%s", r.scenario_id, r.name, r.verdict,
                 f" (judge {r.judge.score}/2)" if r.judge else "")
    return 0 if all(r.verdict == "PASS" for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--scenario",
        action="append",
        help="scenario id to run (repeatable); default: all",
    )
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--list", action="store_true", help="list scenarios")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"isolated backend port (default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--judge-model", default=None,
        help="judge model id (default: EVAL_JUDGE_MODEL env or backend AGENT_MODEL)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
