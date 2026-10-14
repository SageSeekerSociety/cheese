// 「最近去过」：只记在这台浏览器里（localStorage）。
//
// 记的是一条的 id、去过几次、最后一次什么时候，外加名字和去处的一份快照：那一类
// 当下交不出这一条的时候（换了项目、话题还没加载完）照样列得出来。存储读不出来
// （隐私模式、被清理）就当没有记录，面板照常能用；写不进去就不记这一条。
import type { RouteLocationRaw } from 'vue-router'
import type { PaletteItem } from './sources'

import { storageGetJson, storageSetJson } from '@/lib/browserStorage'

const KEY = 'cheesex.palette.recent'
const LIMIT = 30

export interface RecentEntry {
  id: string
  count: number
  at: number
  title: string
  subtitle?: string
  icon: string
  to: RouteLocationRaw
}

export function readRecents(): RecentEntry[] {
  const parsed = storageGetJson<unknown>(KEY)
  return Array.isArray(parsed) ? (parsed as RecentEntry[]) : []
}

/** 记下一次去过。只记有去处的：做完就结束的操作不算「去过」。 */
export function recordVisit(item: PaletteItem, now = Date.now()) {
  if (!item.to) return
  const rest = readRecents().filter((entry) => entry.id !== item.id)
  const before = readRecents().find((entry) => entry.id === item.id)
  const entry: RecentEntry = {
    id: item.id,
    count: (before?.count ?? 0) + 1,
    at: now,
    title: item.title,
    subtitle: item.subtitle,
    icon: item.icon,
    to: item.to,
  }
  // 存不下就不记。
  storageSetJson(KEY, [entry, ...rest].slice(0, LIMIT))
}

/** 去得越多、越近，排得越前；给打分加的那一点。 */
export function recencyBonus(entry: RecentEntry | undefined, now = Date.now()): number {
  if (!entry) return 0
  const days = (now - entry.at) / 86_400_000
  return Math.min(entry.count, 5) * 2 + Math.max(0, 10 - days)
}
