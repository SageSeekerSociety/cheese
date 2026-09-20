/**
 * 平台在房间里说的话，怎么渲染。
 *
 * 房间是个对话，不是监控面板。平台自己的提示（CI 没过、闸门红了、轮次失败、
 * 合并冲突…）过去和真人发言一样平铺、一样字号、想多长就多长，一条能占五六行。
 * 这个模块是**唯一**决定「这条事件长什么样」的地方：给一个 event block，返回
 * 一份渲染指令，ChatPanel 只负责照着画。
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
 *     who: 'platform' | 'cheese' | 'human',     # 谁在管这件事（码，不是文案）
 *     detail: str | None,                       # 原话 / 日志尾巴 / traceback
 *     detail_label: str | None,                 # 展开区标题，如 "CI 日志"
 *     ...已有字段一律保持: platform/action/code/title/retryable/stack/where/request_id/count
 *   }
 *
 * **文案在前端，后端只发码** —— 和 platform_failures.py 的 code + copy contract 同一条规矩。
 *
 * 向后兼容是硬要求：库里存量事件绝大多数 `meta` 是 null 或只有 `action`，它们
 * 必须继续按老样子渲染（居中灰字一行）。所以每一档新行为都以「新字段存在」为
 * 前提，没有新字段就落回 `plain`。
 */
import type { Block } from '../cx_types'
import type { BackendErrorPresentation } from './backendErrorEvent'
import type { PlatformErrorPresentation } from './platformEvents'

import { backendErrorPresentation } from './backendErrorEvent'
import { platformErrorPresentation } from './platformEvents'

/**
 * 这条事件在对话里露不露面。
 *
 * 「露不露面」和「谁写的」是两件事，过去挤在 `author_type` 一个字段里：
 * system 出现、ai 不出现。于是一条确实由芝士产生、又该让人看见的事件无法表达，
 * 而任何想知道作者的人读到的是一个在回答别的问题的字段。现在它自己有一格；没有
 * 这一格的都露面 —— 平台其它所有写入方都是往房间里说话。
 *
 * 对**消息**同样生效：一条通篇没有中文的 AI 消息不占聊天区（后端在落库时就打上
 * 这一格）。它照常存着、照常在历史里，只是聊天区不显示 —— 藏起来，不是删掉。
 */
function showsInRoom(block: Block): boolean {
  return meta(block)?.in_room !== false
}

/** 谁在管这件事。扫一眼不点开就能决定跟不跟自己有关。 */
export type WhoTag = 'platform' | 'cheese' | 'human'

/** 后端每轮算出来的改动摘要（`meta.changeset`）。 */
export interface ChangeSummary {
  filesTotal: number
  added: number
  removed: number
  files: string[]
  filesOmitted: number
}

function changeSummary(block: Block): ChangeSummary | null {
  const raw = meta(block)?.changeset
  if (!raw || typeof raw !== 'object') return null
  const c = raw as Record<string, unknown>
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
  return {
    filesTotal: num(c.files_total),
    added: num(c.added),
    removed: num(c.removed),
    files: Array.isArray(c.files)
      ? c.files.map((f) => String((f as Record<string, unknown>)?.path ?? '')).filter(Boolean)
      : [],
    filesOmitted: num(c.files_omitted),
  }
}

const WHO_LABEL: Record<WhoTag, string> = {
  platform: '平台已处理',
  cheese: '芝士处理中',
  human: '待人工处理',
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

export type PlatformNotice =
  /** 现场抽屉的东西（前端报错），房间里不显示。 */
  | { mode: 'hidden' }
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
  | { mode: 'backend-error'; error: BackendErrorPresentation }
  /** 芝士这轮干的活（更新了文档 / 提交了验收卡…）。 */
  | { mode: 'action'; resource: string; text: string; detail?: string; detailLabel?: string }
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
      occurrences: NoticeOccurrence[]
    }
  /** 老样子：居中、灰、12px、一行。 */
  | { mode: 'plain' }

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function meta(block: Block): Record<string, unknown> | null {
  return (block.meta as Record<string, unknown> | null) ?? null
}

function whoTag(block: Block): WhoTag | '' {
  const who = str(meta(block)?.who)
  return who in WHO_LABEL ? (who as WhoTag) : ''
}

function whoLabel(block: Block): string {
  const who = whoTag(block)
  return who ? WHO_LABEL[who] : ''
}

/**
 * meta.action（结构化）优先，老的 `action:<resource>` ref 仍然认。
 * 从 ChatPanel 搬过来，行为一字不改。
 */
export function actionResource(block: Block): string | null {
  const metaAction = meta(block)?.action
  if (typeof metaAction === 'string') return metaAction
  const r = (block.refs || []).find((x) => x.startsWith('action:'))
  return r ? r.slice('action:'.length) : null
}

/** meta.action 事件的 content 自带主语（张衡/芝士 编辑了文档）；老卡片要补「芝士」。 */
function actionText(block: Block): string {
  return typeof meta(block)?.action === 'string' ? block.content : `芝士${block.content}`
}

/** 卡面上留一句就够了，超过这个长度就切断 —— 切掉的部分原样进展开区，不丢。 */
const LEAD_MAX = 60

/**
 * 取第一句当可见的那一行，其余全部收起来。
 *
 * 中文句号和换行都算断句；一句话本身就超长时按 LEAD_MAX 硬切 —— 卡面高度是产品
 * 约束（≤2-3 行），不能交给内容长度决定。
 *
 * 中文的 。！？ 后面不跟空格，所以它们单独就算断句；英文的 . ! ? 要后跟空白或
 * 结尾才算，否则 "PR #1.2 没过" 会被从小数点上切开。
 */
function splitLead(text: string): { lead: string; rest: string } {
  const body = text.trim()
  if (!body) return { lead: '', rest: '' }

  const match = /[。！？]|\n|[.!?](?=\s|$)/.exec(body)
  let cut = match ? match.index + (body[match.index] === '\n' ? 0 : 1) : body.length
  let ellipsis = false
  if (cut > LEAD_MAX) {
    cut = LEAD_MAX
    ellipsis = true
  }

  const lead = body.slice(0, cut).trim()
  const rest = body.slice(cut).trim()
  return { lead: ellipsis ? `${lead}…` : lead, rest }
}

function occurrenceOf(block: Block): NoticeOccurrence {
  const m = meta(block)
  return {
    line: block.content,
    label: str(m?.detail_label),
    detail: str(m?.detail),
  }
}

/**
 * 这条事件怎么渲染。
 *
 * @param block 事件块；不是 event 的（消息、附件）返回 null，由调用方按消息渲染。
 * @param run   被折叠进这一行的连续同类事件（含 block 自己）。默认只有它自己。
 */
export function platformNotice(block: Block, run: Block[] = [block]): PlatformNotice | null {
  if (block.kind !== 'event') return null

  const m = meta(block)

  // 前端报错属于「现场」抽屉（调试面），不进群聊 —— 和 tool 事件同一条规矩。
  if (str(m?.event_type) === 'frontend_error') return { mode: 'hidden' }

  // 顺序即优先级，和改动前的模板一致：事故卡 > 动作行 > 后端报错 > 折叠行 > 淡行。
  const incident = platformErrorPresentation(block)
  if (incident) {
    const { lead, rest } = splitLead(incident.body)
    const detail = str(m?.detail)
    return {
      mode: 'incident',
      incident,
      lead,
      // 被切掉的正文和 detail 都收进同一个展开区：卡面只留一句，原文一个字不少。
      rest: [rest, detail].filter(Boolean).join('\n\n'),
      detailLabel: str(m?.detail_label),
    }
  }

  const resource = actionResource(block)
  if (resource)
    return {
      mode: 'action',
      resource,
      text: actionText(block),
      detail: str(m?.detail),
      detailLabel: str(m?.detail_label),
    }

  const error = backendErrorPresentation(block)
  if (error) return { mode: 'backend-error', error }

  if (str(m?.event_type) === 'cloud_provisioning') {
    const latest = run[run.length - 1] ?? block
    return {
      mode: 'fold',
      line: latest.content,
      who: whoTag(latest),
      whoLabel: '',
      count: 1,
      occurrences: run.map((item) => ({
        line: item.content,
        label: item.content,
        detail: str(meta(item)?.detail),
      })),
    }
  }

  if (str(m?.detail)) {
    return {
      mode: 'fold',
      line: block.content,
      who: whoTag(block),
      whoLabel: whoLabel(block),
      count: run.length,
      occurrences: run.map(occurrenceOf).filter((o) => o.detail),
    }
  }

  return { mode: 'plain' }
}

/** 连续折叠时，这条事件归哪一类；null = 不参与按类别折叠。 */
function foldKey(block: Block): string | null {
  const m = meta(block)
  // Cloud lifecycle updates share one row even when the final event has no detail.
  if (str(m?.event_type) === 'cloud_provisioning') return 'cloud_provisioning'
  // 只有「折叠行」这一档参与按类别折叠：它有展开区，能把被折进来的每一条原文都
  // 摆出来。事故卡和后端报错各自只有一份正文/traceback，折进去就真丢了；而老事件
  // 压根没有 event_type，误折会把两件不同的事说成一件。
  if (!str(m?.detail)) return null
  const eventType = str(m?.event_type)
  return eventType || null
}

/** 时间线上的一行：要渲染的块，以及被折进它的那一串。 */
export interface NoticeRow {
  block: Block
  run: Block[]
  notice: PlatformNotice | null
}

/**
 * 把原始块流变成时间线的行：藏掉不属于房间的、折叠连续同类的系统事件。
 *
 * 两条折叠规则并存：
 *  1. content 全等 —— 老规则，管住 `编辑了文档` 这类一模一样的连发。只对**两边都
 *     没有 detail** 的事件生效：淡行和动作行没有展开区，把带原话的那条折进去就
 *     真丢了；
 *  2. 同 `meta.event_type` —— 新规则，管住失败类事件。它们带时间、耗时、HTTP 码，
 *     content 永远不相等，规则 1 从来没在它们身上生效过。折进来的每一条原话都摆
 *     在展开区里，一条不少。
 *
 * 只对系统事件生效，消息之间不折叠；中间隔了一条消息，折叠就断。
 */
export function collapseNotices(blocks: Block[]): NoticeRow[] {
  const rows: NoticeRow[] = []
  for (const block of blocks) {
    // 露不露面先问，再问它是什么 —— 这一格从来就不是事件专有的（见 showsInRoom
    // 的注释：它单独立一格，正是因为「谁写的」和「露不露面」是两个问题）。放在
    // 分支外面，芝士自己写的、但不该占住聊天区的那种消息才藏得住。
    if (!showsInRoom(block)) continue
    if (block.kind !== 'event') {
      if (block.kind === 'message' || block.kind === 'attachment') {
        rows.push({ block, run: [block], notice: null })
      }
      continue
    }
    if (str(meta(block)?.event_type) === 'frontend_error') continue

    const prev = rows[rows.length - 1]
    const prevBlock = prev?.block
    if (prevBlock && prevBlock.kind === 'event' && showsInRoom(prevBlock)) {
      const key = foldKey(block)
      const sameType = key !== null && key === foldKey(prevBlock)
      const bothPlain = !str(meta(block)?.detail) && !str(meta(prevBlock)?.detail)
      if (sameType || (bothPlain && prevBlock.content === block.content)) {
        prev.run.push(block)
        continue
      }
    }
    rows.push({ block, run: [block], notice: null })
  }

  for (const row of rows) row.notice = platformNotice(row.block, row.run)
  return foldTurnSummary(rows)
}

/** 这一行是不是「本轮里平台顺手做的事」——够格被折进本轮摘要。 */
function summaryPart(row: NoticeRow): boolean {
  if (row.notice?.mode === 'action' && row.notice.detail) return false
  return row.notice?.mode === 'action' || changeSummary(row.block) !== null
}

/**
 * 把同一轮里连续的动作行和改动摘要折成一行。
 *
 * 只折**同一个 turn_id** 的连续行：一轮的收尾动作本来就是挨着落的，而跨轮合并会
 * 把两次不同的工作说成一次。turn_id 为空的老块不参与（分不出轮次就别猜）。
 */
function foldTurnSummary(rows: NoticeRow[]): NoticeRow[] {
  const out: NoticeRow[] = []
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i]
    const turnId = row.block.turn_id ?? null
    if (!turnId || !summaryPart(row)) {
      out.push(row)
      continue
    }
    let end = i
    while (end + 1 < rows.length && rows[end + 1].block.turn_id === turnId && summaryPart(rows[end + 1])) {
      end += 1
    }
    const run = rows.slice(i, end + 1)
    // 一条孤零零的动作行没什么可折的，保持原样——本轮摘要那一行的存在理由是
    // 「这一轮改了什么」，没有改动摘要时它只是换个壳说同一句话。
    const changes = run.map((r) => changeSummary(r.block)).find((c) => c !== null) ?? null
    if (changes === null && run.length === 1) {
      out.push(row)
      i = end
      continue
    }
    out.push({
      block: run[run.length - 1].block,
      run: run.flatMap((r) => r.run),
      notice: {
        mode: 'turn-summary',
        changes,
        actions: run
          .map((r) => (r.notice?.mode === 'action' ? { resource: r.notice.resource, text: r.notice.text } : null))
          .filter((a): a is { resource: string; text: string } => a !== null),
        turnId,
      },
    })
    i = end
  }
  return out
}
