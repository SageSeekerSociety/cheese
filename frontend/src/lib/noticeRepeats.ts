// 同一个人连着做的几件事（归档了一串频道、又改了几次文档）在时间线上合成一行：
// 收起时一句概括，按种类各说一句；点开在原处列出每一条，每一条的按钮都还在。
//
// 只合淡行和动作行：事故卡、失败折叠行、草稿卡、本轮摘要各自有要单独看的东西。中间
// 隔了一条别的行（消息、别人的操作、上面那几种行），合并就断。
import type { NoticeRow } from './platformNotice'

import { noticeText } from './noticeText'

import { t } from '@/i18n'

/** 概括句说得出是什么的几种操作；别的种类合在「另有 N 项操作」里。 */
type Known = 'archived' | 'unarchived' | 'docEdited'

// 键名写全，不拼。
const PHRASE: Record<Known, string> = {
  archived: 'work.room.notice.repeats.archived',
  unarchived: 'work.room.notice.repeats.unarchived',
  docEdited: 'work.room.notice.repeats.docEdited',
}

const KNOWN: Record<string, Known> = {
  roomArchived: 'archived',
  roomUnarchived: 'unarchived',
  docEdited: 'docEdited',
  doc: 'docEdited',
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function meta(row: NoticeRow): Record<string, unknown> {
  return (row.block.meta ?? {}) as Record<string, unknown>
}

/** 谁做的。平台替一轮写的行署名都是 system，是哪位队友那一轮在 seat 上。 */
function actorKey(row: NoticeRow): string | null {
  const mode = row.notice?.mode
  if (mode !== 'plain' && mode !== 'action') return null
  const m = meta(row)
  return [row.block.author, str(m.agent_id), str(m.seat)].join('\u0000')
}

function knownOf(row: NoticeRow): Known | null {
  const m = meta(row) as { i18n?: { content?: { key?: unknown } }; action?: unknown }
  return KNOWN[str(m.i18n?.content?.key) || str(m.action)] ?? null
}

/** 正文里那个 `<@handle>`（归档那一类带着它），没有就是这一行的署名。 */
function actorOf(row: NoticeRow): string {
  const m = meta(row) as { i18n?: { content?: { params?: { actor?: unknown } } } }
  return str(m.i18n?.content?.params?.actor) || `<@${row.block.author}>`
}

/** 把同一个人连着做的事合成一行；落单的原样留着。 */
export function foldRepeats(rows: NoticeRow[]): NoticeRow[] {
  const out: NoticeRow[] = []
  let start = 0
  for (let i = 1; i <= rows.length; i += 1) {
    const actor = actorKey(rows[start])
    if (i < rows.length && actor !== null && actorKey(rows[i]) === actor) continue
    const members = rows.slice(start, i)
    if (actor === null || members.length < 2) {
      out.push(...members)
    } else {
      const last = members[members.length - 1]
      out.push({
        block: last.block,
        run: members.flatMap((row) => row.run),
        notice: { mode: 'repeats', actor: actorOf(last), rows: members },
      })
    }
    start = i
  }
  return out
}

/**
 * 合起来那一行说的话：「王昌鑫 归档了 3 个频道，编辑了 2 次文档，另有 1 项操作」。
 * 全是说不出名目的那几种时，说最后一条的原话和一共几条。
 */
export function repeatsLine(rows: NoticeRow[], actor: string): string {
  const counts = new Map<Known, number>()
  let others = 0
  for (const row of rows) {
    const known = knownOf(row)
    if (known) counts.set(known, (counts.get(known) ?? 0) + 1)
    else others += 1
  }
  if (!counts.size) {
    return t('work.room.notice.repeats.only', { line: noticeText(rows[rows.length - 1].block), n: rows.length })
  }
  const parts = [...counts].map(([known, n]) => t(PHRASE[known], { n }))
  if (others) parts.push(t('work.room.notice.repeats.others', { n: others }))
  return t('work.room.notice.repeats.line', { actor, parts: parts.join(t('work.room.notice.repeats.sep')) })
}
