// 工具动作的中文文案 — one table for every surface (live action line, worklog,
// …). Keyed by the SHORT tool name (mcp__cheese__ prefix already stripped).
// An unmapped name renders raw — that's the signal to extend the table.

export const TOOL_LABELS: Record<string, string> = {
  // cheese platform actions
  create_subtopic: '拆出子话题',
  update_doc: '更新了文档',
  remember: '记入项目记忆',
  notify: '发送通知',
  request_accept: '递出验收卡',
  return_conclusion: '回流结论',
  pin_milestone: '钉里程碑',
  write_file: '写文件',
  record_decision: '记录决策',
  // native Claude Code tools
  Bash: '执行命令',
  Write: '写文件',
  Edit: '改文件',
  Read: '读文件',
  Glob: '找文件',
  Grep: '搜内容',
  WebSearch: '搜网页',
  WebFetch: '看网页',
  Agent: '派分身去查',
  Task: '派分身去查',
  NotebookEdit: '改笔记本',
  TodoWrite: '更新任务清单',
  BashOutput: '看命令输出',
  KillShell: '停掉命令',
  KillBash: '停掉命令',
  ExitPlanMode: '提交方案待确认',
  AskUserQuestion: '向用户提问',
  Skill: '调用技能',
  ToolSearch: '查找工具',
}

// The one argument worth previewing per tool (matches the backend's table).
const PREVIEW_ARG: Record<string, string> = {
  Bash: 'command',
  Write: 'file_path',
  Edit: 'file_path',
  Read: 'file_path',
  Glob: 'pattern',
  Grep: 'pattern',
  WebSearch: 'query',
  WebFetch: 'url',
  Agent: 'description',
  Task: 'description',
  NotebookEdit: 'notebook_path',
  Skill: 'skill',
  ToolSearch: 'query',
  create_subtopic: 'title',
  update_doc: 'content',
  remember: 'fact',
  notify: 'title',
  request_accept: 'reviewer_handle',
  return_conclusion: 'conclusion',
  pin_milestone: 'title',
  write_file: 'path',
  record_decision: 'decision',
}

export function toolLabel(name: string): string {
  const short = name.replace(/^mcp__cheese__/, '')
  return TOOL_LABELS[short] ?? short
}

// ---- 现场圆点分级: platform action (amber) vs plain work (neutral) ----
// Deterministic by construction — tool-name prefix / a literal `cheese <sub>`
// word pair inside a Bash command. NEVER inferred from natural language.

// The cheese platform tools by their SHORT (mcp__cheese__-stripped) names, as
// they arrive in live tool events and persisted meta.tool.
const PLATFORM_TOOLS = new Set([
  'create_subtopic',
  'update_doc',
  'remember',
  'notify',
  'request_accept',
  'return_conclusion',
  'pin_milestone',
  'write_file',
  'record_decision',
])

const CHEESE_CMD_RE = /\bcheese\s+\w+/

// Live tool events (WS "tool" frames): platform = cheese MCP tool, or a Bash
// command that invokes the in-sandbox `cheese` CLI.
export function isPlatformAction(name: string, input: Record<string, unknown> | null | undefined): boolean {
  const short = name.replace(/^mcp__cheese__/, '')
  if (name.startsWith('mcp__cheese__') || PLATFORM_TOOLS.has(short)) return true
  if (short === 'Bash') return CHEESE_CMD_RE.test(String(input?.command ?? ''))
  return false
}

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

// "搜内容 pattern…" — the live one-liner shown while 芝士 works (Claude Code 风).
export function formatToolAction(name: string, input: Record<string, unknown> | null | undefined): string {
  const short = name.replace(/^mcp__cheese__/, '')
  const label = TOOL_LABELS[short] ?? short
  const key = PREVIEW_ARG[short]
  const raw = key && input ? input[key] : undefined
  if (raw === undefined || raw === null) return label
  const preview = String(raw).split(/\s+/).join(' ').slice(0, 80)
  return preview ? `${label} · ${preview}` : label
}

// Deterministic batch summary, Claude Code style — counts per verb, no model:
// "搜内容×2 · 读文件×5 · 执行命令×1"
export function summarizeActions(labels: string[]): string {
  const counts = new Map<string, number>()
  for (const l of labels) counts.set(l, (counts.get(l) ?? 0) + 1)
  return [...counts.entries()].map(([label, n]) => (n > 1 ? `${label}×${n}` : label)).join(' · ')
}
