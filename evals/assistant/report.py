"""The eval's report: how many tasks were done right, and what they cost.

The cache numbers are computed the way the admin page computes them
(``feature_stats/features/personal_assistant.py``): the share of all prompt
tokens the provider's cache answered, and the median and tenth percentile of
that share per model call.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path


def _pct(a: float, b: float) -> str:
    return f"{a / b:.0%}" if b else "—"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def write(path: Path, rows: list[dict], model: str, judge_model: str) -> None:
    out = [f"# 个人芝士评测（{model}，评审 {judge_model}）", ""]
    for title, part in (
        ("调整用", [r for r in rows if not r["holdout"]]),
        ("留出", [r for r in rows if r["holdout"]]),
        ("全部", rows),
    ):
        scored = [
            r["judge"]["score"]
            for r in part
            if r.get("judge") and r["judge"].get("score") is not None
        ]
        out.append(
            f"- **{title}**：检查全过 {sum(r['passed'] for r in part)}/{len(part)}"
            + (
                f"；评审平均 {sum(scored) / len(scored):.2f}/2（满分 {scored.count(2)} 题）"
                if scored
                else ""
            )
        )
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["ability"]].append(r)
    out += ["", "## 按能力", "", "| 能力 | 检查全过 | 评审 |", "|---|---|---|"]
    for ability, part in sorted(by.items()):
        scored = [
            r["judge"]["score"]
            for r in part
            if r.get("judge") and r["judge"].get("score") is not None
        ]
        out.append(
            f"| {ability} | {sum(r['passed'] for r in part)}/{len(part)} | "
            + (f"{sum(scored)}/{2 * len(scored)}" if scored else "—")
            + " |"
        )

    calls = [c for r in rows for c in r["calls"]]
    prompt = sum(c[0] for c in calls)
    cached = sum(c[1] for c in calls)
    per_call = [c[1] / c[0] for c in calls if c[0]]
    tools = [len(r["tools"]) for r in rows]
    seconds = [r["seconds"] for r in rows]
    gate = [g for r in rows for g in r["gate"]]
    model_cost = sum(r["model_cost_usd"] for r in rows)
    gate_cost = sum(r["gate_cost_usd"] for r in rows)
    median, p10 = _quantile(per_call, 0.5), _quantile(per_call, 0.1)
    out += [
        "",
        "## 花费",
        "",
        f"- 模型调用 {len(calls)} 次（每题平均 {len(calls) / max(len(rows), 1):.1f}），"
        f"工具调用每题平均 {sum(tools) / max(len(rows), 1):.1f} 次、最多 {max(tools, default=0)} 次",
        f"- 提示词 {prompt} token，缓存命中 {_pct(cached, prompt)}；每次调用的命中率中位数 "
        f"{median if median is None else f'{median:.0%}'}，P10 {p10 if p10 is None else f'{p10:.0%}'}",
        f"- 芝士的费用约 ${model_cost:.4f}（只按提示词估算，不含输出），每题约 ${model_cost / max(len(rows), 1):.5f}",
        f"- 授权判断 {len(gate)} 次，直接执行 {sum(g['verdict'] == 'allowed' for g in gate)}，"
        f"请确认 {sum(g['verdict'] == 'asked' for g in gate)}，费用 ${gate_cost:.4f}",
        f"- 用时中位数 {_quantile(seconds, 0.5) or 0:.1f}s，最长 {max(seconds, default=0):.1f}s；"
        f"出错 {sum(len(r['errors']) for r in rows)} 次",
        "",
        "## 没做对的",
        "",
    ]
    for r in rows:
        failed = [n for n, ok in r["checks"] if not ok]
        low = (
            r.get("judge")
            and r["judge"].get("score") is not None
            and r["judge"]["score"] < 2
        )
        if not failed and not low:
            continue
        out.append(f"### {r['id']}（{r['ability']}{'，留出' if r['holdout'] else ''}）")
        out.append("")
        for turn in r["turns"]:
            out.append(f"> 林溪：{turn['question']}")
            out.append(">")
            answer = (turn["answer"] or "（没有回答）").strip().replace("\n", "\n> ")
            out.append(f"> 芝士：{answer[:600]}{'……' if len(answer) > 600 else ''}")
            out.append("")
        if failed:
            out.append(f"- 没过的检查：{'；'.join(failed)}")
        if r.get("judge"):
            out.append(f"- 评审 {r['judge'].get('score')}：{r['judge'].get('reason')}")
        out.append(f"- 工具：{', '.join(r['tools']) or '无'}")
        for g in r["gate"]:
            out.append(
                f"- 授权判断 {g['verdict']}（{g['stage']}）：{g['summary'].splitlines()[0]}"
            )
        if r["errors"]:
            out.append(f"- 出错：{'；'.join(r['errors'])}")
        out.append("")
    path.write_text("\n".join(out) + "\n")
