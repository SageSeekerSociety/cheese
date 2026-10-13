// How the chat timeline groups its rows, and where the unread line falls.
//
// Two questions that are pure functions of the rows about to be drawn, moved out
// of ChatPanel so they can be read (and tested) without a mounted panel:
//
//   - which row starts a speaker's run, which one continues it, and which one
//     "regroups" (the same person speaking again an hour later),
//   - which block the 新消息 line hangs above, counted back from the unread
//     count the host captured before it marked the topic read.
//
// The day separator is the only thing that says *when* a message is: the
// timestamps on rows are HH:mm, and a topic that runs for weeks is a column of
// 09:32 / 14:07 with nothing to tell today's from last week's.
import type { Block } from '../cx_types'

import i18n, { t } from '../i18n'

/** 一天有多少毫秒。算「隔了几天」用整天的边界，不是按 24 小时除。 */
export const DAY_MS = 86_400_000

/**
 * 同一个人连着说的话合并成一段：只有第一条带头像、名字和时间，其余贴在它下面。
 * 断开只有三种情况，断开之后下一条重新带上名字和时间：
 * - 中间隔了别的行（事件、「已派出」标记、新消息线）—— 否则它看上去像挂在那一行
 *   上的续话；
 * - 换了一天 —— 日期线已经横在中间；
 * - 同一天里隔了一小时以上 —— 下午接着上午说的，不该读成一口气说完的。这一种
 *   只空一小档（`regroup`），还是同一个人。
 */
export const REGROUP_GAP_MS = 60 * 60 * 1000

/** A row's relationship to the one above it. */
export type RunEdge = 'start' | 'regroup' | 'cont'

/** A row the grouping rules look at: the block, and nothing else. */
export interface GroupableRow {
  block: Block
}

/**
 * 「这条属于哪一天」只在跨天时说一次。用本地日期而不是 UTC：读的人在哪个时区,
 * 「今天」就该是那个时区的今天。
 */
export function dayKey(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

export function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOf(today) - startOf(d)) / DAY_MS)
  if (days === 0) return t('work.room.chat.today')
  if (days === 1) return t('work.room.chat.yesterday')
  const locale = i18n.global.locale.value
  // zh-CN 的 short weekday 就是「周一」…「周日」。
  if (days < 7 && days > 0) return new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(d)
  const sameYear = d.getFullYear() === today.getFullYear()
  return d.toLocaleDateString(locale, sameYear ? { month: 'long', day: 'numeric' } : undefined)
}

/**
 * blockId → 要画在它上面的那条日期线。第一条也画：翻到时间线顶部的人同样需要
 * 知道这段是什么时候的。
 */
export function dayLabelsFor(rows: readonly GroupableRow[]): Map<string, string> {
  const out = new Map<string, string>()
  let prev: string | null = null
  for (const { block } of rows) {
    const key = dayKey(block.created_at)
    if (key !== prev) out.set(block.id, dayLabel(block.created_at))
    prev = key
  }
  return out
}

/** 同一个人（同一种身份）说的：按 handle 判，头像和名字都不参与。 */
export function sameSpeaker(a: Block, b: Block): boolean {
  return a.author === b.author && a.author_type === b.author_type
}

/**
 * 这一行和上一行的关系。`broken` 说的是中间有没有插进别的行（「已派出」标记、新
 * 消息线）—— 那些行让下面这条必须重新带上名字。
 */
export function runEdgeBetween(prev: Block | undefined, cur: Block, opts: { broken: boolean }): RunEdge {
  if (!prev) return 'start'
  if (prev.kind === 'event' || cur.kind === 'event') return 'start'
  if (opts.broken) return 'start'
  if (!sameSpeaker(prev, cur) || dayKey(prev.created_at) !== dayKey(cur.created_at)) return 'start'
  return Date.parse(cur.created_at) - Date.parse(prev.created_at) >= REGROUP_GAP_MS ? 'regroup' : 'cont'
}

/**
 * 发件箱里的那几条还没有落库时间，按「现在」算：接在自己刚说的那段后面就贴上去。
 * `index > 0` 的那几条接在发件箱上一条下面，不再单独判断。
 */
export function outboxEdgeAfter(last: Block | undefined, opts: { mine: boolean; now?: Date }): RunEdge {
  if (!last || last.kind === 'event') return 'start'
  if (!opts.mine) return 'start'
  const now = opts.now ?? new Date()
  if (dayKey(last.created_at) !== dayKey(now.toISOString())) return 'start'
  return now.getTime() - Date.parse(last.created_at) >= REGROUP_GAP_MS ? 'regroup' : 'cont'
}

/**
 * 新消息线锚在哪条块上。开话题时按当时的未读数往回数一次就冻住 —— 它是「我上次
 * 看到哪儿」的记号，不是一个会跟着新消息跑的游标。
 *
 * 和后端未读口径一致：只数别人发的消息，系统事件不算。
 */
export function unreadAnchorBlock(rows: readonly GroupableRow[], unread: number, author: string): string | null {
  if (!unread || unread <= 0) return null
  let seen = 0
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const b = rows[i].block
    if ((b.kind !== 'message' && b.kind !== 'attachment') || b.author === author) continue
    seen += 1
    if (seen === unread) return b.id
  }
  // 未读比这一页还多：线就画在这一页最老的那条别人的消息上，别装作没有。
  return rows.find((r) => r.block.author !== author && r.block.kind === 'message')?.block.id ?? null
}
