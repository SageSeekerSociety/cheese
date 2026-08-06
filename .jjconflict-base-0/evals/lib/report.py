"""Report writers: results.jsonl (full inputs/outputs, replayable) + report.md."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.lib.records import ScenarioResult

_VERDICT_ICON = {"PASS": "✅ PASS", "PARTIAL": "🟡 PARTIAL", "FAIL": "❌ FAIL"}


def append_jsonl(path: Path, result: ScenarioResult, run_meta: dict[str, Any]) -> None:
    """One self-contained line per scenario — full inputs + evidence + judge
    round-trip, so a single scenario can be reviewed or re-run without this
    process' memory (可复现性军规)."""
    record = {"run": run_meta, **result.to_json()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def write_report(
    path: Path, results: list[ScenarioResult], run_meta: dict[str, Any]
) -> None:
    lines: list[str] = []
    lines.append("# CheeseX Agent Eval Report")
    lines.append("")
    lines.append(f"- run: `{run_meta['run_id']}` ({run_meta['started_at']})")
    lines.append(
        f"- backend: `{run_meta['backend']}` · agent model: "
        f"`{run_meta['agent_model']}` via `{run_meta['gateway']}` · sandbox: "
        f"`{run_meta['sandbox']}`"
    )
    lines.append(f"- judge model: `{run_meta['judge_model']}`")
    lines.append("")
    lines.append("| 场景 | 结果 | Judge (0-2) | 平台检查 | 用时 |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        judge = "—" if r.judge is None else f"{r.judge.score}"
        ok = sum(1 for c in r.checks if c.passed)
        lines.append(
            f"| {r.scenario_id} {r.name} | {_VERDICT_ICON.get(r.verdict, r.verdict)} "
            f"| {judge} | {ok}/{len(r.checks)} | {r.duration_s:.0f}s |"
        )
    lines.append("")

    for r in results:
        lines.append(f"## {r.scenario_id} · {r.name} — {_VERDICT_ICON.get(r.verdict, r.verdict)}")
        lines.append("")
        if r.error:
            lines.append(f"**运行错误**：`{r.error}`")
            lines.append("")
        if r.checks:
            lines.append("### 平台侧检查")
            lines.append("")
            for c in r.checks:
                mark = "✅" if c.passed else "❌"
                lines.append(f"- {mark} `{c.name}` — {c.detail}")
            lines.append("")
        if r.judge is not None:
            lines.append("### Judge")
            lines.append("")
            lines.append(f"- score: **{r.judge.score}/2** (model: `{r.judge.model}`)")
            lines.append(f"- reason: {r.judge.reason}")
            lines.append("")
        excerpt = _excerpt(r)
        if excerpt:
            lines.append("### 关键输入/输出摘录")
            lines.append("")
            lines.append(excerpt)
            lines.append("")
        lines.append(
            f"<sub>完整输入输出见 results.jsonl（scenario={r.scenario_id}）。"
            f"单独重跑：`--scenario {r.scenario_id}`</sub>"
        )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _excerpt(r: ScenarioResult) -> str:
    """A short human-readable slice of the scenario's I/O for the report body
    (the full record lives in results.jsonl)."""
    parts: list[str] = []
    msgs = r.inputs.get("messages") or r.inputs.get("discussion")
    if r.inputs.get("seeded_memories"):
        parts.append(
            "**种入记忆**：\n"
            + "\n".join(f"> - {m}" for m in r.inputs["seeded_memories"])
        )
    if r.inputs.get("question"):
        q = r.inputs["question"]
        parts.append(f"**提问**（{q['author']}，@芝士）：\n> {q['content']}")
    if msgs:
        parts.append(
            "**发送的消息**：\n"
            + "\n".join(f"> - [{m['author']}] {m['content']}" for m in msgs)
        )
    replies = r.evidence.get("ai_replies") or r.evidence.get("opening_messages")
    if replies:
        joined = "\n\n".join(replies)
        if len(joined) > 1200:
            joined = joined[:1200] + "\n\n……（截断，全文见 results.jsonl）"
        quoted = "\n".join(f"> {line}" for line in joined.splitlines())
        parts.append(f"**芝士的回复**：\n{quoted}")
    return "\n\n".join(parts)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
