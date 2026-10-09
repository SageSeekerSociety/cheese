// 一条批注指着文件的哪里，按这个文件自己的单位说（`backend/app/domain/review/comment_place.py`）。
//
// 文本文件按行；Word 按页，幻灯片按页，表格按单元格。存下来的写法是 `L12-L14`、
// `p3`、`s2`、`汇总!C5`，这里把它变成读的人看的那句话。
import type { ReviewComment } from '@/types/reviewComment'

import { t } from '@/i18n'

export type PlaceKind = 'line' | 'page' | 'slide' | 'cell'

const DOCUMENT: Record<string, PlaceKind> = { docx: 'page', pptx: 'slide', xlsx: 'cell', xlsm: 'cell' }

export function placeKind(path: string): PlaceKind {
  const dot = path.lastIndexOf('.')
  return DOCUMENT[dot < 0 ? '' : path.slice(dot + 1).toLowerCase()] ?? 'line'
}

/** 这一条在这一版里指着哪：「第 12–14 行」「第 3 页」「汇总!C5」，指的东西不在了是「原位置已改动」。 */
export function placeLabel(c: ReviewComment): string {
  if (c.state === 'sent' && c.current_line === null) return t('work.room.review.gone')
  const kind = placeKind(c.path)
  if (kind === 'cell') return c.place
  if (kind === 'page' || kind === 'slide') return t('work.room.review.page', { n: c.place.slice(1) })
  const start = c.state === 'draft' ? c.line_start : (c.current_line as number)
  const end = start + (c.line_end - c.line_start)
  return t('work.room.review.lines', { lines: start === end ? `${start}` : `${start}–${end}` })
}
