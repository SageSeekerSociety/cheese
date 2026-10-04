// 工具动作的文案 — the verb table 现场 renders persisted tool events with.
// Keyed by the SHORT tool name (mcp__cheese__ prefix already stripped).
// An unmapped name renders raw — that's the signal to extend the table.
//
// The values are catalog keys, not words: this table says which action it is,
// the catalog (`toolLabels` namespace) says what to call it in the reader's
// language, so the verb and the status words around it are one language. They
// are looked up at display time, inside `toolLabel()`, so a language switch
// re-reads a line already on screen. Every value is a complete literal:
// `catalog.spec.ts` finds a key's readers by scanning for `toolLabels.<key>`.

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
  SendMessage: 'toolLabels.sendMessage',
  TaskStop: 'toolLabels.taskStop',
  NotebookEdit: 'toolLabels.notebookEdit',
  TodoWrite: 'toolLabels.todoWrite',
  BashOutput: 'toolLabels.bashOutput',
  KillShell: 'toolLabels.killShell',
  KillBash: 'toolLabels.killBash',
  ExitPlanMode: 'toolLabels.exitPlanMode',
  AskUserQuestion: 'toolLabels.askUserQuestion',
  Skill: 'toolLabels.skill',
  ToolSearch: 'toolLabels.toolSearch',
  // pi 原生工具 — 全小写，和上面那批一个都不重合。
  bash: 'toolLabels.piBash',
  read: 'toolLabels.piRead',
  write: 'toolLabels.piWrite',
  edit: 'toolLabels.piEdit',
  ls: 'toolLabels.piLs',
  find: 'toolLabels.piFind',
  grep: 'toolLabels.piGrep',
  // 文档里的芝士读房间仓库的 git 记录（remote_execution/machine_git.py）。
  git: 'toolLabels.piGit',
  // 平台工具表（backend/sandbox/cheese 的 PLATFORM_TOOLS）里的每一样，以及
  // pi 房间里由机器上的 CLI 命令变成的工具（`cheese_<命令>`）。一条都不能少：
  // 少一条，现场那一行显示的就是 `cheese_accept_request`，而这是房间里最该看懂
  // 的那一类动作。少了哪条由 backend/tests/unit/test_tool_labels.py 对着工具表
  // 和 CLI 的命令树说出来。
  chat_send: 'toolLabels.chatSend',
  chat_edit: 'toolLabels.chatEdit',
  todo_write: 'toolLabels.todoWrite',
  cheese_chat_list: 'toolLabels.cheeseChatList',
  cheese_chat_search: 'toolLabels.cheeseChatSearch',
  cheese_chat_get: 'toolLabels.cheeseChatGet',
  cheese_chat_replies: 'toolLabels.cheeseChatReplies',
  cheese_doc_set: 'toolLabels.cheeseDocSet',
  cheese_doc_get: 'toolLabels.cheeseDocGet',
  cheese_doc_edit: 'toolLabels.cheeseDocEdit',
  cheese_doc_comment_reply: 'toolLabels.cheeseDocCommentReply',
  cheese_task: 'toolLabels.cheeseTask',
  cheese_worktree: 'toolLabels.cheeseWorktree',
  cheese_sync: 'toolLabels.cheeseSync',
  cheese_recover: 'toolLabels.cheeseRecover',
  cheese_close_task: 'toolLabels.cheeseCloseTask',
  cheese_push_fix: 'toolLabels.cheesePushFix',
  cheese_fetch: 'toolLabels.cheeseFetch',
  cheese_docs_search: 'toolLabels.cheeseDocsSearch',
  cheese_docs_read: 'toolLabels.cheeseDocsRead',
  cheese_lock: 'toolLabels.cheeseLock',
  cheese_unlock: 'toolLabels.cheeseUnlock',
  cheese_decision: 'toolLabels.cheeseDecision',
  cheese_title: 'toolLabels.cheeseTitle',
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
  cheese_sync_agents: 'toolLabels.cheeseSyncAgents',
  cheese_serve: 'toolLabels.cheeseServe',
  cheese_library_ls: 'toolLabels.cheeseLibraryLs',
  cheese_library_get: 'toolLabels.cheeseLibraryGet',
  cheese_mail_attachment: 'toolLabels.cheeseMailAttachment',
  cheese_show: 'toolLabels.cheeseShow',
  cheese_pull: 'toolLabels.cheesePull',
  cheese_template_list: 'toolLabels.cheeseTemplateList',
  cheese_template_new: 'toolLabels.cheeseTemplateNew',
  cheese_convert: 'toolLabels.cheeseConvert',
  cheese_recalc: 'toolLabels.cheeseRecalc',
  cheese_feedback_propose: 'toolLabels.cheeseFeedbackPropose',
  cheese_feedback_list: 'toolLabels.cheeseFeedbackList',
  cheese_feedback_get: 'toolLabels.cheeseFeedbackGet',
  cheese_feedback_claim: 'toolLabels.cheeseFeedbackClaim',
  cheese_feedback_release: 'toolLabels.cheeseFeedbackRelease',
  cheese_machine: 'toolLabels.cheeseMachine',
  cheese_wait_machine: 'toolLabels.cheeseWaitMachine',
  cheese_note: 'toolLabels.cheeseNote',
  cheese_deliver_at: 'toolLabels.cheeseDeliverAt',
  cheese_routine_draft: 'toolLabels.cheeseRoutineDraft',
  cheese_routine_list: 'toolLabels.cheeseRoutineList',
  cheese_routine_update: 'toolLabels.cheeseRoutineUpdate',
  cheese_routine_pause: 'toolLabels.cheeseRoutinePause',
  cheese_routine_report: 'toolLabels.cheeseRoutineReport',
  cheese_method_draft: 'toolLabels.cheeseMethodDraft',
  cheese_method_update: 'toolLabels.cheeseMethodUpdate',
  platform_request: 'toolLabels.platformRequest',
  // 后台任务 — pi 自己没有后台 shell，这五个是平台加的。
  bash_start: 'toolLabels.bashStart',
  bash_read: 'toolLabels.bashRead',
  bash_write: 'toolLabels.bashWrite',
  bash_kill: 'toolLabels.bashKill',
  bash_list: 'toolLabels.bashList',
}

/** `mcp__<服务器>__<工具>` → `<工具>`。**认任意服务器名**，不认某一个写死的：写死
 *  `mcp__cheese__` 的那天，claude_code 的服务器（注册名是 `native`）就已经不是它了，
 *  于是前缀剥不掉、下面那张表查不到，时间线上原样显示
 *  `mcp__native__cheese_feedback_propose` —— 而「前缀没剥掉」和「这个工具本来就没有
 *  中文标签」在屏幕上是同一件事。 */
const MCP_PREFIX = /^mcp__[a-z0-9_]+__/

export function toolLabel(name: string): string {
  const short = name.replace(MCP_PREFIX, '')
  const key: string | undefined = TOOL_LABELS[short]
  return key === undefined ? short : t(key)
}

// ---- 现场圆点分级: platform action (solid) vs plain work (faint) ----
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
