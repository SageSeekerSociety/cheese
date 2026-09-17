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
  // pi 原生工具 — 全小写，和上面那批一个都不重合。
  bash: '执行命令',
  read: '读取文件',
  write: '写入文件',
  edit: '修改文件',
  ls: '列出目录',
  find: '查找文件',
  grep: '搜索内容',
  // 平台 CLI 的每条命令，在 pi 房间里各是一个工具（`cheese_<命令>`）。
  // 一条都不能少：少一条，现场那一行显示的就是 `cheese_accept_request`，
  // 而这是房间里最该看懂的那一类动作。少了哪条由
  // backend/tests/unit/test_tool_labels.py 直接对着 CLI 的命令树说出来。
  cheese_chat_send: '发布消息',
  chat_send: '发布消息', // 系统提示里用的名字，两个都注册了
  cheese_doc_set: '更新实况文档',
  cheese_doc_get: '读取实况文档',
  cheese_split: '创建任务',
  cheese_worktree: '准备工作目录',
  cheese_sync: '同步任务代码',
  cheese_bind: '认领任务',
  cheese_close_task: '关闭任务',
  cheese_push_fix: '更新任务 PR',
  cheese_fetch: '读取网页',
  cheese_lock: '占用重资源',
  cheese_unlock: '释放重资源',
  cheese_decision: '记录决策',
  cheese_title: '设置标题',
  cheese_remember: '记入项目记忆',
  cheese_recall: '检索项目记忆',
  cheese_notify: '发送通知',
  cheese_ask: '向用户提问',
  cheese_accept_request: '提交验收卡',
  cheese_describe: '修改验收说明',
  cheese_ready: '标记可评审',
  cheese_tell: '给分身留言',
  cheese_milestone: '添加里程碑',
  cheese_members: '列出话题成员',
  cheese_gh_token: '获取 GitHub 令牌',
  cheese_status: '查看平台状态',
  cheese_serve: '设置预览',
  cheese_artifact: '设置交付物',
  cheese_api: '调用平台接口',
  // 后台任务 — pi 自己没有后台 shell，这五个是平台加的。
  bash_start: '启动后台任务',
  bash_read: '读取任务输出',
  bash_write: '向任务输入',
  bash_kill: '终止后台任务',
  bash_list: '列出后台任务',
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
