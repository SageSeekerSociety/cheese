import type { ChapterRow } from '@/types/chapters'

/**
 * 章节按对话里的读序排：起头那条消息在时间线上的位置。服务端已经按这个顺序给，
 * 这里再排一次，是因为「第几章」是画出来的人数的——数据哪天不是这个顺序，编号
 * 就会和对话对不上。
 */
export function inReadingOrder(rows: readonly ChapterRow[]): ChapterRow[] {
  return [...rows].sort(
    (a, b) => a.block.created_at.localeCompare(b.block.created_at) || a.block.id.localeCompare(b.block.id)
  )
}
