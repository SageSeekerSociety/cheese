import type { Block, ListPayload } from '../cx_types'

/** One page of a room's 现场. */
export type SitePage = ListPayload<Block> & {
  has_more?: boolean
  oldest_id?: string | null
  /**
   * 从这一页往后读的游标（下一次用 `after` 传它）：这一页最新那条的游标。
   *
   * `oldest_id` 说的是「上面还有」，往回翻用它；`newest_id` 说的是「接着从这里
   * 问下去」，刷新用它。`after` 那一路上 `has_more` / `oldest_id` 一律不动，
   * 下面还有没有由 `has_newer` 说。
   */
  newest_id?: string | null
  /** `after` 的那一路上：这一页之后还有更新的。 */
  has_newer?: boolean
  /** When each turn on the page started: turn id → ISO time. */
  turn_starts?: Record<string, string>
}
