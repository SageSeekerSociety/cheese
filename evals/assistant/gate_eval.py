"""The authorization check against its labelled set (``gate_cases.py``).

Runs the platform's own ``gate.judge`` — the rules, the quick look, the second
look, the parsing — on each case, with the person's rules and what was said
handed in place of the database, and the eval's gateway key. No backend, no
database.

    uv run --project backend python evals/assistant/gate_eval.py
    uv run --project backend python evals/assistant/gate_eval.py --model glm-5.2 --repeat 3

What it reports: how many were right; 漏放 (an action that should have been
asked about was done) and 误拦 (an action that should have been done became a
card), by kind; how many ended at each stage; tokens, the share the cache
answered, cost and time. Holdout cases are reported apart.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.assistant import boot  # noqa: E402
from evals.assistant.gate_cases import CASES, GateCase  # noqa: E402


def _line(said: str) -> str:
    for mark, label in (
        ("已执行：", "芝士已执行"),
        ("已请确认：", "芝士已请用户确认"),
        ("例行：", "用户（建这条例行事务时说）"),
    ):
        if said.startswith(mark):
            return f"[{label}] {said.removeprefix(mark)}"
    return f"[用户] {said}"


async def _judge(case: GateCase, chat, gate, models) -> dict:
    rules = models.AssistantRules(
        user_id=0,
        ask=list(gate.DEFAULT_ASK if case.ask is None else case.ask),
        allow_text=case.allow_text,
        ask_text=case.ask_text,
    )

    async def rules_of(session, user_id):
        return rules

    async def said_and_done(session, conversation_id):
        return [_line(s) for s in case.said]

    started = time.monotonic()
    # judge reads both through these two; each case runs alone under them.
    gate.rules_of, gate._said_and_done = rules_of, said_and_done
    verdict = await gate.judge(
        None,  # type: ignore[arg-type]  (both readers are replaced above)
        user_id=0,
        conversation_id=None,
        action=gate.Action(
            case.action_kind, case.action, case.categories, low=case.low
        ),
        chat=chat,
    )
    return {
        "id": case.id,
        "kind": case.kind,
        "holdout": case.holdout,
        "expected": "allow" if case.allow else "ask",
        "got": "allow" if verdict.allowed else "ask",
        "right": verdict.allowed == case.allow,
        "stage": verdict.stage,
        "reason": verdict.reason,
        "prompt_tokens": verdict.usage.prompt_tokens,
        "cache_read_tokens": verdict.usage.cache_read_tokens,
        "completion_tokens": verdict.usage.completion_tokens,
        "cost_usd": verdict.cost_usd,
        "seconds": round(time.monotonic() - started, 2),
        "case": asdict(case),
    }


def _share(a: int, b: int) -> str:
    return f"{a / b:.0%}" if b else "—"


def report(rows: list[dict], model: str) -> str:
    out = [f"# 授权判断评测（{model}）", ""]
    for title, part in (
        ("调整用", [r for r in rows if not r["holdout"]]),
        ("留出", [r for r in rows if r["holdout"]]),
        ("全部", rows),
    ):
        should_ask = [r for r in part if r["expected"] == "ask"]
        should_allow = [r for r in part if r["expected"] == "allow"]
        missed = [r for r in should_ask if r["got"] == "allow"]
        blocked = [r for r in should_allow if r["got"] == "ask"]
        out += [
            f"## {title}：{sum(r['right'] for r in part)}/{len(part)} 正确",
            "",
            f"- 漏放（该确认却直接执行）：{len(missed)}/{len(should_ask)}"
            + (f"：{', '.join(r['id'] for r in missed)}" if missed else ""),
            f"- 误拦（该直接执行却请确认）：{len(blocked)}/{len(should_allow)}"
            + (f"：{', '.join(r['id'] for r in blocked)}" if blocked else ""),
            "",
        ]
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
    out += ["## 按类型", "", "| 类型 | 正确 |", "|---|---|"]
    out += [
        f"| {k} | {sum(r['right'] for r in v)}/{len(v)} |"
        for k, v in sorted(by_kind.items())
    ]
    stages = Counter(r["stage"] for r in rows)
    prompt = sum(r["prompt_tokens"] for r in rows)
    cached = sum(r["cache_read_tokens"] for r in rows)
    looked = [r["seconds"] for r in rows if r["stage"] in ("quick", "reasoned")]
    looked.sort()
    out += [
        "",
        "## 花费",
        "",
        f"- 结束于：{', '.join(f'{k} {v}' for k, v in sorted(stages.items()))}",
        f"- 提示词 {prompt} token，缓存命中 {_share(cached, prompt)}；输出 "
        f"{sum(r['completion_tokens'] for r in rows)} token",
        f"- 费用 ${sum(r['cost_usd'] for r in rows):.4f}"
        f"（平均每次 ${sum(r['cost_usd'] for r in rows) / max(len(rows), 1):.5f}）",
        f"- 用时中位数 {looked[len(looked) // 2] if looked else 0:.1f}s，最长 "
        f"{looked[-1] if looked else 0:.1f}s",
        "",
        "## 判错的",
        "",
    ]
    for r in rows:
        if not r["right"]:
            out += [
                f"- **{r['id']}**（{r['kind']}，应{'执行' if r['expected'] == 'allow' else '确认'}，"
                f"{r['stage']}）：{r['case']['said'][-1]} → {r['case']['action'].splitlines()[0]}"
                + (f"；理由：{r['reason']}" if r["reason"] else ""),
            ]
    # The person reads the reason on the card: it should name what is missing.
    out += ["", "## 确认的理由", ""]
    for r in rows:
        if r["right"] and r["got"] == "ask" and r["stage"] == "reasoned":
            out += [f"- {r['id']}：{r['reason']}"]
    return "\n".join(out) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", default=None, help="default: settings.assistant_model"
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", nargs="*", help="case ids")
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parent.parent / "results"
    )
    args = parser.parse_args()

    key = boot.gateway_key()
    os.environ["LLM_GATEWAY_ADMIN_BASE"] = boot.GATEWAY
    sys.path.insert(0, str(boot.BACKEND))
    from app.core.config import settings
    from app.domain.assistant import gate, models
    from app.domain.gateway_chat import GatewayChat

    model = args.model or settings.assistant_model
    settings.assistant_model = model
    chat = GatewayChat(key, model, max_tokens=gate.QUICK_TOKENS)
    cases = [c for c in CASES if not args.only or c.id in args.only]
    rows = []
    for round_ in range(args.repeat):
        for case in cases:
            row = await _judge(case, chat, gate, models)
            row["round"] = round_
            rows.append(row)
            print(
                f"{case.id} {row['expected']:>5} → {row['got']:<5} {row['stage']:<8}"
                f" {'ok' if row['right'] else 'WRONG'} {row['reason'][:60]}",
                flush=True,
            )
    run = args.out / f"gate-{time.strftime('%Y%m%d-%H%M%S')}-{model}"
    run.mkdir(parents=True, exist_ok=True)
    with (run / "results.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (run / "report.md").write_text(report(rows, model))
    print(f"\n{run / 'report.md'}")
    print(report(rows, model))


if __name__ == "__main__":
    asyncio.run(main())
