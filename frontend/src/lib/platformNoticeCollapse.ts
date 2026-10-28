// 原始块流怎么变成时间线上的行：哪些块露面、连续同类的折成一行、折完之后谁还署名。
//
// 判一条 block 长什么样在 platformNoticeCards.ts；这里管的是「一串 block 怎么排」：
// 藏掉不该占聊天区的、把连续的同类事件折进一行、收回已经过时的署名，最后交给
// noticeRepeats 合并同一个人连着做的同一种操作。
import type { Block } from '../cx_types'
import type { NoticeRow } from './platformNotice'

import { foldRepeats } from './noticeRepeats'
import { changeSummary, mailOutcomes, platformNotice, whoTag } from './platformNoticeCards'

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function meta(block: Block): Record<string, unknown> | null {
  return (block.meta as Record<string, unknown> | null) ?? null
}

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

/** 连续折叠时，这条事件归哪一类；null = 不参与按类别折叠。 */
function foldKey(block: Block): string | null {
  const m = meta(block)
  // 每封草稿是一张要单独确认的卡，折在一起就只剩一张能点。
  if (str(m?.event_type) === 'mail_drafted') return null
  // 只有「折叠行」这一档参与按类别折叠：它有展开区，能把被折进来的每一条原文都
  // 摆出来。事故卡和后端报错各自只有一份正文/traceback，折进去就真丢了；而老事件
  // 压根没有 event_type，误折会把两件不同的事说成一件。
  if (!str(m?.detail)) return null
  const eventType = str(m?.event_type)
  return eventType || null
}

/**
 * 说「这件任务在这里开始」的那种行（`task_created`、更早的 `split`）：它画成那件任务的
 * 卡，一行一件。两件都是没起名时新建的，正文一字不差（「创建了任务「新任务」」），按
 * 正文折就把后一件的卡整张吞了。
 */
function startsTask(block: Block): boolean {
  const action = meta(block)?.action
  return action === 'task_created' || action === 'split'
}

/**
 * 一张验收卡从递上来到合并（或作废）一路上的那些提示。它们说的是同一件事 —— 这张卡
 * 现在停在哪、归谁 —— 所以后一句会让前一句过时。
 */
const CARD_EVENTS = new Set([
  'card_filed',
  'card_rejected',
  'card_voided',
  'card_redescribed',
  'ci_failed',
  'ci_passed',
  'gate_failed',
  'gate_blocked',
  'gate_abandoned',
  'pr_review',
  'pr_conflict',
  'upstream_conflict',
  'merge_refused',
  'merge_withheld',
  'migration_collision',
  'accept_ready',
  'accept_conflict',
  'accept_stopped',
  'accept_dismissed',
  'accept_done',
  'force_merged',
  'pr_closed',
])

function aboutTheCard(block: Block): boolean {
  return block.kind === 'event' && CARD_EVENTS.has(str(meta(block)?.event_type))
}

/**
 * 卡上的「谁在管这件事」只对最新的那一句成立。
 *
 * `who` 是写下这一行那一刻的事实：「等 alice 采纳 · 等人处理」「退回了 · 芝士正在
 * 处理」。之后这段对话里关于这张卡又有一句说了现在归谁（芝士重新递了卡、PR 合了），
 * 前一句的尾标就过时了 —— 留着它，一张早已采纳的卡还在说等人手动处理。所以卡的那
 * 些提示里，尾标只留在最后一句说了归谁的那一行上，更早的只剩那件事本身。别的提示
 * （某位队友这一轮怎么了）各说各的事，不受影响。
 */
function settleOwners(rows: NoticeRow[]): void {
  let superseded = false
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const row = rows[i]
    if (!aboutTheCard(row.block)) continue
    if (superseded && row.notice?.mode === 'fold') row.notice = { ...row.notice, who: '', whoLabel: '' }
    if (row.run.some((block) => aboutTheCard(block) && whoTag(block))) superseded = true
  }
}

/** 这一行是不是「本轮里平台顺手做的事」——够格被折进本轮摘要。 */
function summaryPart(row: NoticeRow): boolean {
  if (row.notice?.mode === 'action' && row.notice.detail) return false
  // 带着「查看改动 / 查看建议」的文档行要有自己的按钮，不折进摘要。
  if (row.notice?.mode === 'action' && (row.notice.docEdits || row.notice.docSuggestions || row.notice.libraryDoc))
    return false
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
    while (
      end + 1 < rows.length &&
      rows[end + 1].block.turn_id === turnId &&
      rows[end + 1].block.author === row.block.author &&
      str(meta(rows[end + 1].block)?.agent_id) === str(meta(row.block)?.agent_id) &&
      summaryPart(rows[end + 1])
    ) {
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

/**
 * 把原始块流变成时间线的行：藏掉不属于房间的、折叠连续同类的系统事件。
 *
 * 两条折叠规则并存：
 *  1. content 全等 —— 老规则，管住 `编辑了文档` 这类一模一样的连发。只对**两边都
 *     没有 detail** 的事件生效：淡行和动作行没有展开区，把带原话的那条折进去就
 *     真丢了；新建任务的行也不折，每一行是一件任务的卡；
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
      // 白名单，所以每加一种块都要在这里认一次——`artifact`（芝士摆出来给人看的
      // 一份东西，`cheese show`）就是漏在这儿的：后端一直往时间线写它，这一行一直
      // 不认，于是它连一个字都到不了屏幕上。人在房间里等着看那份报告，而唯一的
      // 线索是右边预览格上一个小点。
      if (block.kind === 'message' || block.kind === 'attachment' || block.kind === 'artifact') {
        rows.push({ block, run: [block], notice: null })
      }
      continue
    }
    const prev = rows[rows.length - 1]
    const prevBlock = prev?.block
    if (prevBlock && prevBlock.kind === 'event' && showsInRoom(prevBlock)) {
      const key = foldKey(block)
      const sameType = key !== null && key === foldKey(prevBlock)
      const bothPlain =
        !str(meta(block)?.detail) && !str(meta(prevBlock)?.detail) && !startsTask(block) && !startsTask(prevBlock)
      // 平台替一轮写的通知署名都是 system：是哪位队友的那一轮在 `seat` 上，两位的
      // 并成一行，后一位的就署成了前一位。
      const sameAuthor =
        prevBlock.author === block.author &&
        prevBlock.author_type === block.author_type &&
        str(meta(prevBlock)?.agent_id) === str(meta(block)?.agent_id) &&
        str(meta(prevBlock)?.seat) === str(meta(block)?.seat)
      if (sameAuthor && (sameType || (bothPlain && prevBlock.content === block.content))) {
        prev.run.push(block)
        continue
      }
    }
    rows.push({ block, run: [block], notice: null })
  }

  // 草稿卡片的下落记在后来的 `mail_result` 事件里，只有看得到整条块流的这里对得上。
  const mailEnds = mailOutcomes(blocks)
  for (const row of rows) {
    row.notice = platformNotice(row.block, row.run)
    if (row.notice?.mode === 'mail-draft') row.notice.outcome = mailEnds.get(row.notice.mail.draftId) ?? null
  }
  settleOwners(rows)
  return foldRepeats(foldTurnSummary(rows))
}

/**
 * 这一块在房间里画得出来吗——和 `collapseNotices` 同一套判据（不露面的、前端错误、
 * 不在白名单里的都画不出任何一行）。
 *
 * 时间线窗口（`useTimeline`）用它只装画得出来的块。装进去却不画的块白占窗口额度：
 * 上限一满，`capNewest` 会把「最新的一截」收进背后供「回到最新」换上来，而一串不画
 * 的块正好塞满这一截时，换上来就是一屏空的，最新那条看得见的消息反被挤丢。数的对象
 * 换成画得出来的那些，这个问题就没有了。
 */
export function rendersInRoom(block: Block): boolean {
  return collapseNotices([block]).length > 0
}
