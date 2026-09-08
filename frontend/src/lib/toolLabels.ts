// 工具动作的中文文案 — the verb table 现场 renders persisted tool events with.
// Keyed by the SHORT tool name (mcp__cheese__ prefix already stripped).
// An unmapped name renders raw — that's the signal to extend the table.

export const TOOL_LABELS: Record<string, string> = {
  // cheese platform actions
  update_doc: '更新文档',
  remember: '记入项目记忆',
  notify: '发送通知',
  request_accept: '提交验收卡',
  pin_milestone: '添加里程碑',
  write_file: '写入文件',
  record_decision: '记录决策',
  // native Claude Code tools
  Bash: '执行命令',
  Write: '写入文件',
  Edit: '修改文件',
  Read: '读取文件',
  Glob: '查找文件',
  Grep: '搜索内容',
  WebSearch: '搜索网页',
  WebFetch: '读取网页',
  Agent: '派出分身',
  Task: '派出分身',
  NotebookEdit: '修改笔记本',
  TodoWrite: '更新任务清单',
  BashOutput: '查看命令输出',
  KillShell: '终止命令',
  KillBash: '终止命令',
  ExitPlanMode: '提交方案待确认',
  AskUserQuestion: '向用户提问',
  Skill: '调用技能',
  ToolSearch: '查找工具',
}

export function toolLabel(name: string): string {
  const short = name.replace(/^mcp__cheese__/, '')
  return TOOL_LABELS[short] ?? short
}

// ---- 现场圆点分级: platform action (amber) vs plain work (neutral) ----
// Deterministic by construction — never inferred from natural language.

// Structured event payload persisted on kind=event blocks (backend meta).
export interface EventMeta {
  tool?: string
  arg?: string
  platform?: boolean
}

// Persisted event blocks: the structured meta decides; legacy rows without
// meta fall back to the refs action-tag (our own structured token). Rows with
// neither stay neutral — no guessing from the baked content text.
export function isPlatformEvent(meta: EventMeta | null | undefined, refs: string[] | null | undefined): boolean {
  if (meta?.platform === true) return true
  return (refs ?? []).some((r) => r.startsWith('action:'))
}
