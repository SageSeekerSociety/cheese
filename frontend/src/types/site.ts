import type { Block, ListPayload } from '../cx_types'

/** One page of a room's 现场. */
export type SitePage = ListPayload<Block> & {
  has_more?: boolean
  oldest_id?: string | null
  /** When each turn on the page started: turn id → ISO time. */
  turn_starts?: Record<string, string>
}
