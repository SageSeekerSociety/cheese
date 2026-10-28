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

import { partialStringField } from './partialJson'

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
  // 平台工具表（backend/sandbox/cheese 的 PLATFORM_TOOLS）里的每一样和它的每个
  // 动词。一条都不能少：少一条，现场那一行显示的就是 `cheese_card`，而这是房间里
  // 最该看懂的那一类动作。少了哪条由 backend/tests/unit/test_tool_labels.py 对着
  // 工具表说出来。`cheese_<命令>` 那几条（`cheese_worktree` 等）给旧记录用：pi
  // 房间曾把 CLI 的命令当工具，现在和别的骨架一样在 bash 里跑 `cheese …`。
  chat_send: 'toolLabels.chatSend',
  'cheese_chat.edit': 'toolLabels.chatEdit',
  todo_write: 'toolLabels.todoWrite',
  'cheese_chat.list': 'toolLabels.cheeseChatList',
  'cheese_chat.search': 'toolLabels.cheeseChatSearch',
  'cheese_chat.get': 'toolLabels.cheeseChatGet',
  'cheese_chat.replies': 'toolLabels.cheeseChatReplies',
  // 一个对象工具的名字本身：调用的 action 还没写出来时（现场那一帧、只报了名字的
  // 一步）说的是它。每个动词各有自己的词。
  cheese_chat: 'toolLabels.cheeseChat',
  cheese_doc: 'toolLabels.cheeseDoc',
  cheese_app: 'toolLabels.cheeseApp',
  cheese_canvas: 'toolLabels.cheeseCanvas',
  cheese_room: 'toolLabels.cheeseRoom',
  cheese_card: 'toolLabels.cheeseCard',
  cheese_feedback: 'toolLabels.cheeseFeedback',
  cheese_skill: 'toolLabels.cheeseSkill',
  cheese_session: 'toolLabels.cheeseSession',
  cheese_web: 'toolLabels.cheeseWeb',
  cheese_library: 'toolLabels.cheeseLibrary',
  cheese_problem: 'toolLabels.cheeseProblem',
  cheese_inbox: 'toolLabels.cheeseInbox',
  'cheese_memory.save': 'toolLabels.cheeseMemory',
  'cheese_memory.read': 'toolLabels.cheeseMemory',
  'cheese_memory.forget': 'toolLabels.cheeseMemory',
  'cheese_memory.project': 'toolLabels.cheeseMemoryProject',
  'cheese_library.read': 'toolLabels.cheeseLibraryRead',
  'cheese_doc.set': 'toolLabels.cheeseDocSet',
  'cheese_doc.get': 'toolLabels.cheeseDocGet',
  'cheese_doc.edit': 'toolLabels.cheeseDocEdit',
  'cheese_doc.new': 'toolLabels.cheeseDocNew',
  'cheese_doc.list': 'toolLabels.cheeseDocList',
  'cheese_app.save': 'toolLabels.cheeseAppSave',
  'cheese_app.get': 'toolLabels.cheeseAppGet',
  'cheese_canvas.get': 'toolLabels.cheeseCanvasGet',
  'cheese_canvas.edit': 'toolLabels.cheeseCanvasEdit',
  cheese_doc_comment_reply: 'toolLabels.cheeseDocCommentReply',
  'cheese_task.open': 'toolLabels.cheeseTask',
  cheese_worktree: 'toolLabels.cheeseWorktree',
  cheese_checkout: 'toolLabels.cheeseCheckout',
  cheese_sync: 'toolLabels.cheeseSync',
  cheese_transcript: 'toolLabels.cheeseTranscript',
  cheese_recover: 'toolLabels.cheeseRecover',
  'cheese_task.close': 'toolLabels.cheeseCloseTask',
  'cheese_task.reopen': 'toolLabels.cheeseReopenTask',
  'cheese_task.state': 'toolLabels.cheeseTaskState',
  cheese_push_fix: 'toolLabels.cheesePushFix',
  'cheese_web.fetch': 'toolLabels.cheeseFetch',
  // 个人芝士的工具（backend/sandbox/cheese 的 DELEGATED_TOOLS）。
  'cheese_inbox.list': 'toolLabels.cheeseInbox',
  'cheese_inbox.respond': 'toolLabels.cheeseInboxRespond',
  cheese_read: 'toolLabels.cheeseRead',
  cheese_find: 'toolLabels.cheeseFind',
  cheese_help: 'toolLabels.cheeseHelp',
  'cheese_card.decide': 'toolLabels.cheeseReviewDecide',
  cheese_people: 'toolLabels.cheeseMemberSet',
  // 成员管理改成 cheese_people 之前的调用。
  'cheese_room.member': 'toolLabels.cheeseMemberSet',
  'cheese_room.create': 'toolLabels.cheeseChannelCreate',
  'cheese_room.set': 'toolLabels.cheeseSettingsSet',
  'cheese_space.set': 'toolLabels.cheeseSettingsSet',
  'cheese_space.announce': 'toolLabels.cheeseSpaceAnnounce',
  'cheese_space.category': 'toolLabels.cheeseSpaceCategory',
  'cheese_space.code': 'toolLabels.cheeseSpaceCode',
  'cheese_space.review': 'toolLabels.cheeseSpaceReview',
  cheese_settings: 'toolLabels.cheeseSettingsSet',
  cheese_routine: 'toolLabels.cheeseRoutine',
  cheese_todo: 'toolLabels.cheeseTodo',
  cheese_subagent_start: 'toolLabels.agent',
  cheese_subagent_send: 'toolLabels.cheeseSubagentSend',
  cheese_subagent_stop: 'toolLabels.cheeseSubagentStop',
  cheese_memory: 'toolLabels.cheeseMemory',
  'cheese_library.give': 'toolLabels.cheeseFileGive',
  'cheese_library.take': 'toolLabels.cheeseFileToComputer',
  'cheese_web.search': 'toolLabels.cheeseWebSearch',
  cheese_my_tasks: 'toolLabels.cheeseMyTasks',
  cheese_task_search: 'toolLabels.cheeseTaskSearch',
  cheese_task_get: 'toolLabels.cheeseTaskGet',
  'cheese_problem.write': 'toolLabels.cheeseProblemWrite',
  'cheese_problem.publish': 'toolLabels.cheeseProblemPublish',
  cheese_message_draft: 'toolLabels.cheeseMessageDraft',
  cheese_project_search: 'toolLabels.cheeseProjectSearch',
  cheese_task_draft: 'toolLabels.cheeseTaskDraft',
  cheese_file_read: 'toolLabels.cheeseFileRead',
  cheese_space_materials: 'toolLabels.cheeseSpaceMaterials',
  cheese_decision: 'toolLabels.cheeseDecision',
  'cheese_room.rename': 'toolLabels.cheeseTitle',
  cheese_notify: 'toolLabels.cheeseNotify',
  cheese_ask: 'toolLabels.cheeseAsk',
  'cheese_card.request': 'toolLabels.cheeseAcceptRequest',
  'cheese_card.describe': 'toolLabels.cheeseDescribe',
  'cheese_card.redeliver': 'toolLabels.cheeseRedeliver',
  'cheese_card.ready': 'toolLabels.cheeseReady',
  cheese_milestone: 'toolLabels.cheeseMilestone',
  'cheese_room.members': 'toolLabels.cheeseMembers',
  cheese_gh_token: 'toolLabels.cheeseGhToken',
  'cheese_session.status': 'toolLabels.cheeseStatus',
  cheese_serve: 'toolLabels.cheeseServe',
  'cheese_library.list': 'toolLabels.cheeseLibraryLs',
  cheese_library_get: 'toolLabels.cheeseLibraryGet',
  cheese_mail_attachment: 'toolLabels.cheeseMailAttachment',
  cheese_show: 'toolLabels.cheeseShow',
  cheese_pull: 'toolLabels.cheesePull',
  cheese_template_list: 'toolLabels.cheeseTemplateList',
  cheese_template_new: 'toolLabels.cheeseTemplateNew',
  cheese_convert: 'toolLabels.cheeseConvert',
  cheese_check: 'toolLabels.cheeseCheck',
  cheese_recalc: 'toolLabels.cheeseRecalc',
  'cheese_feedback.propose': 'toolLabels.cheeseFeedbackPropose',
  'cheese_feedback.list': 'toolLabels.cheeseFeedbackList',
  'cheese_feedback.get': 'toolLabels.cheeseFeedbackGet',
  'cheese_feedback.claim': 'toolLabels.cheeseFeedbackClaim',
  'cheese_feedback.release': 'toolLabels.cheeseFeedbackRelease',
  cheese_run_records: 'toolLabels.cheeseRunRecords',
  'cheese_session.environment': 'toolLabels.cheeseEnvironment',
  'cheese_session.wait': 'toolLabels.cheeseWaitMachine',
  'cheese_session.compact': 'toolLabels.cheeseCompact',
  'cheese_session.note': 'toolLabels.cheeseNote',
  'cheese_session.deliver_at': 'toolLabels.cheeseDeliverAt',
  'cheese_routine.create': 'toolLabels.cheeseRoutineDraft',
  'cheese_routine.list': 'toolLabels.cheeseRoutineList',
  'cheese_routine.update': 'toolLabels.cheeseRoutineUpdate',
  'cheese_routine.pause': 'toolLabels.cheeseRoutinePause',
  'cheese_routine.report': 'toolLabels.cheeseRoutineReport',
  'cheese_skill.propose': 'toolLabels.cheeseSkillDraft',
  'cheese_skill.update': 'toolLabels.cheeseSkillUpdate',
  platform_request: 'toolLabels.platformRequest',
  // 技能库：按关键词检索已装技能，以及读一份技能的完整 SKILL.md。
  'cheese_skill.search': 'toolLabels.skillsSearch',
  'cheese_skill.read': 'toolLabels.skillsRead',
  // 后台任务 — pi 自己没有后台 shell，这五个是平台加的。
  bash_start: 'toolLabels.bashStart',
  bash_read: 'toolLabels.bashRead',
  bash_write: 'toolLabels.bashWrite',
  bash_kill: 'toolLabels.bashKill',
  bash_list: 'toolLabels.bashList',
  // pi 的 codemode：模型写一段脚本，脚本里再调别的工具（现场把那几样挂在它下面）。
  codemode: 'toolLabels.codemode',
}

/** `mcp__<服务器>__<工具>` → `<工具>`。**认任意服务器名**，不认某一个写死的：写死
 *  `mcp__cheese__` 的那天，claude_code 的服务器（注册名是 `native`）就已经不是它了，
 *  于是前缀剥不掉、下面那张表查不到，时间线上原样显示
 *  `mcp__native__cheese_feedback_propose` —— 而「前缀没剥掉」和「这个工具本来就没有
 *  中文标签」在屏幕上是同一件事。 */
const MCP_PREFIX = /^mcp__[a-z0-9_-]+?__/

/** `mcp__<服务器>__<工具>` 里的服务器名；不是 MCP 工具就是空串。 */
export function mcpServerOf(name: string): string {
  return /^mcp__([a-z0-9_-]+?)__/.exec(name)?.[1] ?? ''
}

/** 并进某个对象、成了它一个动词的旧名字，和它变成的那个动作（`<工具>.<action>`）；还在表上、
 *  后来才收 `action` 的名字也在，给它收 action 以前的那些调用。记录里的调用永远是当时
 *  的写法，所以这张表一直留着。它是 `backend/app/domain/session_record/tool_keys.py` 里 `RETIRED` 的
 *  副本，两份由 backend/tests/unit/test_tool_labels.py 对齐。 */
export const RETIRED: Record<string, string> = {
  cheese_task: 'cheese_task.open',
  cheese_task_state: 'cheese_task.state',
  cheese_close_task: 'cheese_task.close',
  cheese_reopen_task: 'cheese_task.reopen',
  cheese_task_open: 'cheese_task.open',
  cheese_accept_request: 'cheese_card.request',
  cheese_describe: 'cheese_card.describe',
  cheese_redeliver: 'cheese_card.redeliver',
  cheese_ready: 'cheese_card.ready',
  cheese_review_decide: 'cheese_card.decide',
  cheese_chat_list: 'cheese_chat.list',
  cheese_chat_search: 'cheese_chat.search',
  cheese_chat_get: 'cheese_chat.get',
  cheese_chat_replies: 'cheese_chat.replies',
  chat_edit: 'cheese_chat.edit',
  cheese_message_send: 'chat_send',
  cheese_doc_get: 'cheese_doc.get',
  cheese_doc_set: 'cheese_doc.set',
  cheese_doc_edit: 'cheese_doc.edit',
  cheese_doc_new: 'cheese_doc.new',
  cheese_doc_list: 'cheese_doc.list',
  cheese_app_save: 'cheese_app.save',
  cheese_app_get: 'cheese_app.get',
  cheese_canvas_get: 'cheese_canvas.get',
  cheese_canvas_edit: 'cheese_canvas.edit',
  cheese_members: 'cheese_room.members',
  cheese_title: 'cheese_room.rename',
  cheese_channel_create: 'cheese_room.create',
  cheese_member_set: 'cheese_people',
  cheese_routine_draft: 'cheese_routine.create',
  cheese_routine_list: 'cheese_routine.list',
  cheese_routine_update: 'cheese_routine.update',
  cheese_routine_pause: 'cheese_routine.pause',
  cheese_routine_report: 'cheese_routine.report',
  cheese_feedback_propose: 'cheese_feedback.propose',
  cheese_feedback_list: 'cheese_feedback.list',
  cheese_feedback_get: 'cheese_feedback.get',
  cheese_feedback_claim: 'cheese_feedback.claim',
  cheese_feedback_release: 'cheese_feedback.release',
  skills_search: 'cheese_skill.search',
  skills_read: 'cheese_skill.read',
  cheese_skill_draft: 'cheese_skill.propose',
  cheese_skill_update: 'cheese_skill.update',
  cheese_status: 'cheese_session.status',
  cheese_environment: 'cheese_session.environment',
  cheese_wait_environment: 'cheese_session.wait',
  cheese_compact: 'cheese_session.compact',
  cheese_note: 'cheese_session.note',
  cheese_deliver_at: 'cheese_session.deliver_at',
  cheese_library_ls: 'cheese_library.list',
  cheese_fetch: 'cheese_web.fetch',
  cheese_web_search: 'cheese_web.search',
  cheese_web_fetch: 'cheese_web.fetch',
  cheese_inbox_respond: 'cheese_inbox.respond',
  cheese_problem_write: 'cheese_problem.write',
  cheese_problem_publish: 'cheese_problem.publish',
  cheese_attachment_read: 'cheese_library.read',
  cheese_file_give: 'cheese_library.give',
  cheese_file_to_computer: 'cheese_library.take',
  cheese_memory_read: 'cheese_memory.project',
  cheese_docs_search: 'cheese_help',
  cheese_docs_read: 'cheese_help',
  cheese_settings_set: 'cheese_settings',
  cheese_notify_me: 'cheese_notify',
}

/** 调用里的 `action`：记录里是解析好的参数，现场那一帧是还在写的 JSON 原文，只认
 *  已经写完的那个值。 */
function actionOf(args: unknown): string {
  if (typeof args === 'string') {
    const field = partialStringField(args, 'action')
    return field?.closed ? field.text : ''
  }
  if (args !== null && typeof args === 'object') {
    const action = (args as Record<string, unknown>).action
    return typeof action === 'string' ? action : ''
  }
  return ''
}

/** 一次调用是哪个动作：一个对象的几个动词之一是 `cheese_task.close`，按退役的名字
 *  做的调用读成它后来的那个动作，只做一件事的工具就是它的名字。 */
export function toolKey(name: string, args?: unknown): string {
  const short = name.replace(MCP_PREFIX, '')
  const action = actionOf(args)
  if (action) return `${short}.${action}`
  return RETIRED[short] ?? short
}

/** 这一步的动词。`args` 给了，就按它是哪个动作取词；动作没有自己的词时用工具的。 */
export function toolLabel(name: string, args?: unknown): string {
  const short = name.replace(MCP_PREFIX, '')
  const key: string | undefined = TOOL_LABELS[toolKey(short, args)] ?? TOOL_LABELS[short]
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
