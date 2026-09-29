// 输入变成一组一组的结果。
//
// 不打字：等你处理的、最近去过的、此刻能做的操作。打字：最好的那一条单独放在最上面，
// 其余按类分组，每组只列前几条。输入以 # @ > 开头时只看对应的那一类。
import type { RecentEntry } from './recents'
import type { PaletteItem, PaletteSource, Prefix, SourceContext } from './sources'

import { match } from './match'
import { recencyBonus } from './recents'

export interface ResultRow extends PaletteItem {
  /** 名字里对上输入的那一段，高亮用。 */
  range: [number, number] | null
}

export interface ResultGroup {
  key: string
  /** i18n key */
  label: string
  rows: ResultRow[]
}

const PER_GROUP = 5
const PREFIXES: Prefix[] = ['#', '@', '>']

export function splitPrefix(input: string): { prefix: Prefix | null; query: string } {
  const head = input.trimStart()[0] as Prefix | undefined
  if (head && PREFIXES.includes(head)) return { prefix: head, query: input.trimStart().slice(1).trim() }
  return { prefix: null, query: input.trim() }
}

function plain(item: PaletteItem): ResultRow {
  return { ...item, range: null }
}

export function buildResults(
  input: string,
  sources: PaletteSource[],
  ctx: SourceContext,
  recents: RecentEntry[],
  now = Date.now()
): ResultGroup[] {
  const { prefix, query } = splitPrefix(input)
  const active = prefix ? sources.filter((source) => source.prefix === prefix) : sources
  const perSource = active.map((source) => ({ source, items: source.items(ctx) }))
  const recentById = new Map(recents.map((entry) => [entry.id, entry]))

  if (!query && !prefix) {
    const groups: ResultGroup[] = []
    const all = perSource.flatMap(({ items }) => items)
    const byId = new Map(all.map((item) => [item.id, item]))
    const awaiting = all.filter((item) => item.awaiting).slice(0, 3)
    if (awaiting.length)
      groups.push({ key: 'awaiting', label: 'navigation.palette.awaiting', rows: awaiting.map(plain) })
    // 最近去过的，那一类当下交得出就用当下的（名字可能改过），交不出就用记下的快照。
    const recent = recents
      .filter((entry) => !awaiting.some((item) => item.id === entry.id))
      .slice(0, PER_GROUP)
      .map((entry) => plain(byId.get(entry.id) ?? { ...entry }))
    if (recent.length) groups.push({ key: 'recent', label: 'navigation.palette.recent', rows: recent })
    const commands = perSource.find(({ source }) => source.prefix === '>')
    if (commands?.items.length)
      groups.push({
        key: commands.source.id,
        label: commands.source.label,
        rows: commands.items.slice(0, PER_GROUP).map(plain),
      })
    return groups
  }

  const scored = perSource.map(({ source, items }) => ({
    source,
    rows: items
      .flatMap((item) => {
        const hit = match(query, item.title, item.keywords)
        return hit
          ? [{ row: { ...item, range: hit.range }, score: hit.score + recencyBonus(recentById.get(item.id), now) }]
          : []
      })
      .sort((a, b) => b.score - a.score),
  }))

  const best = scored.flatMap(({ rows }) => rows.slice(0, 1)).sort((a, b) => b.score - a.score)[0]
  const groups: ResultGroup[] = []
  // 只看一类的时候不另立「最匹配」：整张表就是那一类，最好的本来就在第一行。
  if (best && !prefix && query) groups.push({ key: 'best', label: 'navigation.palette.best', rows: [best.row] })
  for (const { source, rows } of scored) {
    // 只打了前缀（「>」）是要看这一类的全部，不截。
    const rest = rows.filter((entry) => entry !== best || prefix || !query).slice(0, query ? PER_GROUP : undefined)
    if (rest.length) groups.push({ key: source.id, label: source.label, rows: rest.map((entry) => entry.row) })
  }
  return groups
}
