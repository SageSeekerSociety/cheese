// 工具动作的文案 — the verb table 现场 renders persisted tool events with.
// Keyed by the SHORT tool name (mcp__cheese__ prefix already stripped).
// An unmapped name renders raw — that's the signal to extend the table.
//
// The values are **catalog keys**, not words: this table says which action it is,
// the catalog says what to call it in the reader's language. They are looked up
// at DISPLAY time — inside `toolLabel()`, never once at module load — so a language
// switch re-reads a line that is already on screen.
//
// Every value is a complete literal, and the table spells them out
// (`toolLabels.updateDoc`). `catalog.spec.ts` decides whether a key is dead by
// scanning the source for `namespace.key`; a key assembled from a variable or a
// template string reads as unused there and turns the suite red.
//
// Catalogue layout and gates: docs/i18n.md §5, docs/i18n-authoring.md §3.

import { t } from '@/i18n'

export const TOOL_LABELS: Record<string, string> = {
  // cheese platform actions
  update_doc: 'toolLabels.updateDoc',
  remember: 'toolLabels.remember',
  notify: 'toolLabels.notify',
  request_accept: 'toolLabels.requestAccept',
  pin_milestone: 'toolLabels.pinMilestone',
  write_file: 'toolLabels.writeFile',
  record_decision: 'toolLabels.recordDecision',
  // native Claude Code tools
  Bash: 'toolLabels.bash',
  Write: 'toolLabels.write',
  Edit: 'toolLabels.edit',
  Read: 'toolLabels.read',
  Glob: 'toolLabels.glob',
  Grep: 'toolLabels.grep',
  WebSearch: 'toolLabels.webSearch',
  WebFetch: 'toolLabels.webFetch',
  Agent: 'toolLabels.agent',
  Task: 'toolLabels.task',
  NotebookEdit: 'toolLabels.notebookEdit',
  TodoWrite: 'toolLabels.todoWrite',
  BashOutput: 'toolLabels.bashOutput',
  KillShell: 'toolLabels.killShell',
  KillBash: 'toolLabels.killBash',
  ExitPlanMode: 'toolLabels.exitPlanMode',
  AskUserQuestion: 'toolLabels.askUserQuestion',
  Skill: 'toolLabels.skill',
  ToolSearch: 'toolLabels.toolSearch',
  // pi 原生工具 — 全小写，和上面那批的工具名一个都不重合。词表里的键带 `pi`
  // 前缀：`Bash` 和 `bash` 是两套 harness 各有的一个工具，去掉前缀后键会撞在
  // 一起，而这两个名字必须各留一条。
  bash: 'toolLabels.piBash',
  read: 'toolLabels.piRead',
  write: 'toolLabels.piWrite',
  edit: 'toolLabels.piEdit',
  ls: 'toolLabels.piLs',
  find: 'toolLabels.piFind',
  grep: 'toolLabels.piGrep',
  // 平台 CLI 的每条命令，在 pi 房间里各是一个工具（`cheese_<命令>`）。
  // 一条都不能少：少一条，现场那一行显示的就是 `cheese_accept_request`，
  // 而这是房间里最该看懂的那一类动作。少了哪条由
  // backend/tests/unit/test_tool_labels.py 直接对着 CLI 的命令树说出来。
  cheese_chat_send: 'toolLabels.cheeseChatSend',
  chat_send: 'toolLabels.chatSend', // 系统提示里用的名字，两个都注册了
  cheese_doc_set: 'toolLabels.cheeseDocSet',
  cheese_doc_get: 'toolLabels.cheeseDocGet',
  cheese_split: 'toolLabels.cheeseSplit',
  cheese_worktree: 'toolLabels.cheeseWorktree',
  cheese_sync: 'toolLabels.cheeseSync',
  cheese_bind: 'toolLabels.cheeseBind',
  cheese_close_task: 'toolLabels.cheeseCloseTask',
  cheese_push_fix: 'toolLabels.cheesePushFix',
  cheese_fetch: 'toolLabels.cheeseFetch',
  cheese_lock: 'toolLabels.cheeseLock',
  cheese_unlock: 'toolLabels.cheeseUnlock',
  cheese_decision: 'toolLabels.cheeseDecision',
  cheese_title: 'toolLabels.cheeseTitle',
  cheese_remember: 'toolLabels.cheeseRemember',
  cheese_recall: 'toolLabels.cheeseRecall',
  cheese_notify: 'toolLabels.cheeseNotify',
  cheese_ask: 'toolLabels.cheeseAsk',
  cheese_accept_request: 'toolLabels.cheeseAcceptRequest',
  cheese_describe: 'toolLabels.cheeseDescribe',
  cheese_ready: 'toolLabels.cheeseReady',
  cheese_tell: 'toolLabels.cheeseTell',
  cheese_milestone: 'toolLabels.cheeseMilestone',
  cheese_members: 'toolLabels.cheeseMembers',
  cheese_gh_token: 'toolLabels.cheeseGhToken',
  cheese_status: 'toolLabels.cheeseStatus',
  cheese_serve: 'toolLabels.cheeseServe',
  cheese_artifact: 'toolLabels.cheeseArtifact',
  cheese_convert: 'toolLabels.cheeseConvert',
  cheese_recalc: 'toolLabels.cheeseRecalc',
  cheese_api: 'toolLabels.cheeseApi',
  // 后台任务 — pi 自己没有后台 shell，这五个是平台加的。
  bash_start: 'toolLabels.bashStart',
  bash_read: 'toolLabels.bashRead',
  bash_write: 'toolLabels.bashWrite',
  bash_kill: 'toolLabels.bashKill',
  bash_list: 'toolLabels.bashList',
}

export function toolLabel(name: string): string {
  const short = name.replace(/^mcp__cheese__/, '')
  // 查不到就把原名还给调用方 —— 现场显示一个内部拼法，正是这张表该加一行的信号。
  const key: string | undefined = TOOL_LABELS[short]
  return key === undefined ? short : t(key)
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
