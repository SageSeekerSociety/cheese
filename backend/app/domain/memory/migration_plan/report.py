"""这份迁移的报告——人复核的就是它。

`render_report` 只读 `MigrationPlan`，一个字都不改它。每一节都要能自己站住：去处
一条条列出来、要写的文件全文附上、只建议和丢掉的分开写，最后一行说清下一步。
"""

from .plan import REPORT_TITLE, TEAM_INDEX_GOAL_LINES, Destination, MigrationPlan


def render_report(plan: MigrationPlan) -> str:
    """这份迁移的报告。**它就是人复核的那份东西**，所以每一节都得能自己站住。"""
    by_id = {entry.source_id: entry for entry in plan.sources}
    lines: list[str] = [
        f"# {REPORT_TITLE}",
        "",
        f"- 项目：`{plan.project_id}`",
        f"- 旧记忆：**{len(plan.sources)} 条**，全部有去处",
        f"- 要写：**{len(plan.files)} 个文件**（新建 "
        f"{sum(1 for f in plan.files if f.is_new)} 个、合并 "
        f"{sum(1 for f in plan.files if not f.is_new)} 个），"
        f"索引加 {sum(index.added_lines for index in plan.indexes)} 行",
        f"- 只建议、不自动改：{len(plan.suggestions)} 条",
        "",
        "## team 的索引 {#index}",
        "",
        f"迁移后 **{plan.team_index_lines()} 行**（目标 ≤ {TEAM_INDEX_GOAL_LINES} 行）"
        + (" —— **没达标，还要再合并一批。**" if plan.over_goal() else " —— 达标。"),
        "",
        "## 一条一条的去处 {#each}",
        "",
        "| 旧记忆 | 从哪儿来 | 去处 | 目标 | 一句话理由 |",
        "|---|---|---|---|---|",
    ]
    for decision in plan.decisions:
        entry = by_id[decision.source_id]
        target = decision.target or decision.target_path() or "—"
        lines.append(
            f"| `{entry.excerpt(60)}` | {entry.where} | {decision.destination.value} "
            f"| `{target}` | {decision.reason} |"
        )
    lines += ["", "## 谁扔了什么 {#counts}", ""]
    counts = plan.counts()
    lines.append(
        "、".join(f"{name} {count} 条" for name, count in counts.items() if count)
        or "（没有决定）"
    )
    lines += ["", "## 要写的文件 {#files}", ""]
    for planned in plan.files:
        verb = "新建" if planned.is_new else "合并（追加正文）"
        lines += [
            f"### `{planned.prefixed}`（{verb}，{len(planned.sources)} 条旧记忆）",
            "",
            "```markdown",
            planned.content.rstrip(),
            "```",
            "",
        ]
    if plan.suggestions:
        lines += [
            "## 只是建议，不自动改 {#suggest}",
            "",
            "下面这些不属于记忆。要动它们，是另一件要人点头的事。",
            "",
        ]
        for suggestion in plan.suggestions:
            entry = by_id[suggestion.source_id]
            text = suggestion.text or f"把「{entry.excerpt()}」写进去"
            lines.append(f"- `{suggestion.target}`：{text}（{suggestion.reason}）")
        lines.append("")
    discarded = [
        decision
        for decision in plan.decisions
        if decision.destination is Destination.discard
    ]
    if discarded:
        lines += ["## 丢掉 {#discard}", ""]
        for decision in discarded:
            entry = by_id[decision.source_id]
            lines.append(f"- `{entry.excerpt()}` —— {decision.reason}")
        lines += [
            "",
            "丢掉的是**不再搬**：旧表一个字都不删（它留着只读），这一条只是不变成新"
            "树里的一条记忆。",
            "",
        ]
    lines += [
        "## 下一步 {#next}",
        "",
        "复核这份报告、点了头之后才会写进 `memory_files`。承认之前，一条都不用改。",
        "",
    ]
    return "\n".join(lines)
