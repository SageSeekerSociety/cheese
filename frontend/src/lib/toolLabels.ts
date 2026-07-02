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
}

export function toolLabel(name: string): string {
  const short = name.replace(/^mcp__cheese__/, '')
  return TOOL_LABELS[short] ?? short
}

// "搜内容 pattern…" — the live one-liner shown while 芝士 works (Claude Code 风).
export function formatToolAction(
  name: string,
  input: Record<string, unknown> | null | undefined,
): string {
  const short = name.replace(/^mcp__cheese__/, '')
  const label = TOOL_LABELS[short] ?? short
  const key = PREVIEW_ARG[short]
  const raw = key && input ? input[key] : undefined
  if (raw === undefined || raw === null) return label
  const preview = String(raw).split(/\s+/).join(' ').slice(0, 80)
  return preview ? `${label} · ${preview}` : label
}
