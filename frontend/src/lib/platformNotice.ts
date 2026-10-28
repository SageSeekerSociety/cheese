/**
 * 平台在房间里说的话，怎么渲染。
 *
 * 房间是个对话，不是监控面板。平台自己的提示（CI 没过、闸门红了、轮次失败、
 * 合并冲突…）过去和真人发言一样平铺、一样字号、想多长就多长，一条能占五六行。
 * 这个模块决定事件的正文、详情和操作。ChatPanel 根据作者与事件归属，给 agent
 * 相关通知加上统一的状态消息外观。
 *
 * 硬约束是「信息不能丢，只能收起来」—— 所以没有任何一档会把原文扔掉：长文进
 * `<details>` 的展开区，连续同类事件折成一行时，每一次的原文都还在展开区里。
 *
 * 契约（后端按这个发，见父话题「让房间干净下来」）：
 *
 *   kind=event
 *   content = 一行人话，≤40 字
 *   meta = {
 *     in_room: bool | None,                     # 露不露面；缺省 = 露面，见 showsInRoom
 *     event_type: str,                          # 类别码
 *     severity: 'info' | 'warn' | 'error',
 *     who: 'platform' | 'cheese' | 'human',     # 谁在管这件事（码，不是文案）；卡的提示只有最新的一句画出来
 *     detail: str | None,                       # 原话 / 日志尾巴 / traceback
 *     detail_label: str | None,                 # 展开区标题，如 "CI 日志"
 *     ...已有字段一律保持: platform/action/code/title/retryable/stack/where/request_id/count
 *   }
 *
 * **文案在前端，后端只发码** —— 和 platform_failures.py 的 code + copy contract 同一条规矩。
 * 平台自己说的那几格（content / detail / detail_label / title）后端存的是中文原句，
 * 另在 `meta.i18n` 里带着它的键和参数；显示时一律经 `noticeText()` 按读者的语言渲染。
 *
 * 缺少结构化字段的事件仍保留原文；没有作者或类别依据时，不猜它属于哪个 agent。
 *
 * 这一份是契约的形状、平台自己那几种事件的清单，和调用方取东西的入口：一条 block 怎么
 * 判成某一种卡、`meta` 里的载荷怎么读在 `platformNoticeCards.ts`，一串 block 怎么折成
 * 时间线上的行在 `platformNoticeCollapse.ts`。
 */
import type { Block } from '../cx_types'
import type { DocEdit } from './docEdits'
import type { PlatformErrorPresentation } from './platformEvents'

/** 谁在管这件事。扫一眼不点开就能决定跟不跟自己有关。 */
export type WhoTag = 'platform' | 'cheese' | 'human'

/** 本轮改动里的一个文件：路径和它自己的增删行数。 */
export interface ChangedFile {
  path: string
  added: number
  removed: number
}

/** 后端每轮算出来的改动摘要（`meta.changeset`）。 */
export interface ChangeSummary {
  filesTotal: number
  added: number
  removed: number
  files: ChangedFile[]
  filesOmitted: number
}

/** 折叠成一行的那一堆里，每一次各自的原文 —— 一次都不能丢。 */
export interface NoticeOccurrence {
  /** 这一次的那行人话。 */
  line: string
  /** 展开区标题，如「CI 日志」；后端没给就是空串。 */
  label: string
  /** 原话 / 日志尾巴 / traceback。 */
  detail: string
}

/** 芝士写好、等邮箱主人确认的一封邮件（`meta.mail`）。 */
export interface MailDraftView {
  draftId: string
  owner: string
  /** 写这封草稿的队友（座位 handle）；旧的卡没有这一位。 */
  draftedBy: string
  account: string
  to: string[]
  cc: string[]
  subject: string
  body: string
  attachments: { name: string; size: number | null }[]
}

/** 那封草稿后来怎样了（`mail_result` 事件）。 */
export interface MailOutcome {
  status: 'sent' | 'failed' | 'discarded'
  sentAt: string | null
  reason: string | null
}

/** 平台替哪位 AI 队友写的这一条：名字给人读，handle 给头像定颜色。 */
export interface NoticeAgent {
  name: string
  handle: string | null
}

export type PlatformNotice =
  /** 现场抽屉的东西（前端报错），房间里不显示。 */
  /** 芝士写好的一封邮件：在房间里看全、由邮箱主人在这里确认发送。 */
  | { mode: 'mail-draft'; mail: MailDraftView; outcome: MailOutcome | null }
  /** 基础设施事故卡：正文压成一行，剩下的进展开区。 */
  | {
      mode: 'incident'
      incident: PlatformErrorPresentation
      /** 卡面上唯一可见的一句。 */
      lead: string
      /** 被收起来的剩余正文 + detail，展开才看得到。 */
      rest: string
      detailLabel: string
    }
  /** 后端报错：本来就是目标形态，原样保留（它是这套东西的样板）。 */
  /** 芝士这轮干的活（更新了文档 / 提交了验收卡…）。 */
  | {
      mode: 'action'
      resource: string
      text: string
      detail?: string
      detailLabel?: string
      /** 文档：这一串改动改了哪几处（逐处改的才有）。 */
      docEdits?: DocEdit[]
      /** 文档：AI 队友提的修改建议有几处。 */
      docSuggestions?: number
      /** 项目资料库里的一份文档：AI 队友在这个对话里新建了它或者改了它。 */
      libraryDoc?: LibraryDocCard
    }
  /**
   * 本轮摘要 (spec §8.5 变更提醒): 这一轮改了什么，外加它顺带动过的平台资源。
   *
   * 一轮里「更新了文档」「记录了决策」「提交了验收卡」各占一行，说的全是右边那栏
   * 自己会亮的事；而**这一轮到底改了哪些文件**——房间里唯一没有别处可看的东西
   * ——过去只在现场里躺着一行灰字。合成一行，噪音反而少了三行。
   */
  | {
      mode: 'turn-summary'
      changes: ChangeSummary | null
      /** 同一轮里的动作行，按发生顺序；每条带自己的按钮资源码。 */
      actions: { resource: string; text: string }[]
      /** 「查看改动」要打开的那一轮。 */
      turnId: string | null
    }
  /** 折叠行：一行 summary + ×N + who 尾标，原文在展开区。 */
  | {
      mode: 'fold'
      line: string
      /** 谁在管这件事的**码**——形态统一之后，只有它决定这一行的记号颜色。 */
      who: WhoTag | ''
      whoLabel: string
      /** >1 时显示 ×N；1 表示没有折叠。 */
      count: number
      /** 一个人自己写的那句话（退回的理由），跟着这一行直接写出来，不收进展开区。 */
      quote?: string
      occurrences: NoticeOccurrence[]
      /** 后端说现在点一下重试有用（`meta.retryable`）。 */
      retryable: boolean
    }
  /** 同一个人连着做的同一种操作，合成一行；点开列出每一条（lib/noticeRepeats）。 */
  | { mode: 'repeats'; actor: string; rows: NoticeRow[] }
  /** 老样子：居中、灰、12px、一行。 */
  | { mode: 'plain' }

// These events describe the room agent's work or execution environment. `who`
// instead names the next responder, so it cannot decide the message's identity.
export const AGENT_STATUS_EVENTS = new Set([
  'machine_provisioning',
  'turn_failed',
  'turn_timeout',
  'deploy_interrupted',
  'dispatch_unknown',
  'sandbox_rebuilt',
  'host_failure',
  'platform_error',
  'subagent_start',
  'ci_failed',
  'gate_failed',
  'gate_blocked',
  'gate_abandoned',
  'accept_conflict',
  'upstream_conflict',
  'pr_conflict',
  'pr_review',
  'merge_refused',
  'card_filed',
  'artifact_declared',
  'card_rejected',
  'card_voided',
  'card_redescribed',
  'deploy_done',
  'deploy_failed',
  'main_checks_failed',
  'room_merge',
  'conclusion_settled',
  'archive_deferred',
  'accept_done',
  'accept_stopped',
  'accept_ready',
  'accept_dismissed',
  'merge_withheld',
  'pr_closed',
  'force_merged',
  'migration_collision',
  'doc_missing',
])

/** 聊天里那张文档卡：哪一份、是不是刚建的、这一串改了哪几处。 */
export interface LibraryDocCard {
  id: string
  title: string
  created: boolean
  edits: DocEdit[]
  /** 提了几处修改建议（没有直接改）。 */
  suggestions: number
}

/** 芝士整理出来、等人确认的那一件技能：它在哪一页、哪一条。 */
export interface ConfirmTarget {
  name: 'project-skills'
  params: { projectId: string }
  query: Record<string, string>
}

/** 「环境准备失败」那一行指向的地方：项目设置的「环境」，先看出事的那个频道。 */
export interface EnvironmentTarget {
  name: 'project-settings'
  params: { projectId: string; section: 'environment' }
  query: Record<string, string>
}

/** 时间线上的一行：要渲染的块，以及被折进它的那一串。 */
export interface NoticeRow {
  block: Block
  run: Block[]
  notice: PlatformNotice | null
}

/**
 * 每一行是哪一行。合成的行画的是最后一条（时间、正文），可新来一条并进去、或往上翻
 * 页时更早的一条并进来，它都还是原来那一行——按画的那一块认、或按第一块认，人点开
 * 的那一串都会被换成一行新的、收起来的。
 *
 * 所以一行沿用它上一次的名字：`before` 是上一次每一块落在哪一行的名字，这一行里
 * 任何一块上次有名字、且这次没被别的行先用掉，就还叫它；全是新块才用自己的第一块。
 * 返回这次每一行的名字，和下一次要传回来的 `before`。
 */
export function rowKeys(
  rows: NoticeRow[],
  before: ReadonlyMap<string, string>
): { keys: string[]; byBlock: Map<string, string> } {
  const keys: string[] = []
  const byBlock = new Map<string, string>()
  const taken = new Set<string>()
  for (const row of rows) {
    const named = row.run.map((block) => before.get(block.id))
    const own = row.run.map((block) => block.id)
    const key = [...named, ...own].find((k): k is string => !!k && !taken.has(k)) ?? `${row.block.id}#${keys.length}`
    taken.add(key)
    keys.push(key)
    for (const block of row.run) byBlock.set(block.id, key)
  }
  return { keys, byBlock }
}

export { actionResource, confirmTarget, environmentTarget, mailOutcomes, platformNotice } from './platformNoticeCards'
export { collapseNotices, rendersInRoom } from './platformNoticeCollapse'
