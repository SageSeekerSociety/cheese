// 输入变成一组一组的结果。
//
// 不打字：等你处理的、最近去过的、此刻能做的操作。打字：最好的那一条单独放在最上面，
// 其余按类分组，每组只列前几条；远程搜回来的内容跟在后面。输入以 # @ > ? 开头时只看
// 对应的那一类。
import type { RecentEntry } from './recents'
import type { PaletteItem, PaletteSource, Prefix, SourceContext } from './sources'

import { match } from './match'
import { recencyBonus } from './recents'

export interface ResultRow extends PaletteItem {
  /** 名字里对上输入的那一段，高亮用。 */
  range: [number, number] | null
  /** 远程搜回来的一条：它不是一个「地方」，不记进「最近去过」。 */
  remote?: true
}

export interface ResultGroup {
  key: string
  /** i18n key */
  label: string
  rows: ResultRow[]
}

const PER_GROUP = 5
// 打了 ? 只看内容时每组多给几条，但不是全部：一组列得比一屏还长，就该换个词搜了。
const PER_GROUP_CONTENT_ONLY = 10
const PREFIXES: Prefix[] = ['#', '@', '>', '?']

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
  remote: ReadonlyMap<string, PaletteItem[]> = new Map(),
  now = Date.now()
): ResultGroup[] {
  const { prefix, query } = splitPrefix(input)
  const active = prefix ? sources.filter((source) => source.prefix === prefix) : sources
  const perSource = active.flatMap((source) => (source.items ? [{ source, items: source.items(ctx) }] : []))
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
  for (const source of active) {
    const found = remote.get(source.id)
    if (!source.search || !found?.length) continue
    groups.push({
      key: source.id,
      label: source.label,
      rows: found
        .slice(0, prefix ? PER_GROUP_CONTENT_ONLY : PER_GROUP)
        .map((item) => ({ ...aroundHit(item, query), remote: true })),
    })
  }
  return groups
}

// 摘要往往比一行长，命中的词在后半截就会被省略号吃掉（手机上一行只有二十来个字）。
// 从命中处往前留几个字开始显示。
const LEAD = 8

function aroundHit(item: PaletteItem, query: string): Omit<ResultRow, 'remote'> {
  const range = firstWord(item.title, query)
  if (!range || range[0] <= LEAD + 1) return { ...item, range }
  const cut = range[0] - LEAD
  return { ...item, title: `…${item.title.slice(cut)}`, range: [range[0] - cut + 1, range[1] - cut + 1] }
}

/** 标题里最先出现的那个词：后端按词找，摘要里哪个词先出现就亮哪个。 */
function firstWord(title: string, query: string): [number, number] | null {
  const lowered = title.toLowerCase()
  let best: [number, number] | null = null
  for (const word of query.toLowerCase().split(/\s+/).filter(Boolean)) {
    const at = lowered.indexOf(word)
    if (at >= 0 && (!best || at < best[0])) best = [at, at + word.length]
  }
  return best
}

/** 这次输入该去问哪几个远程数据源：打了字，而且没限定成只看某一类本地的东西。 */
export function remoteSources(input: string, sources: PaletteSource[]): PaletteSource[] {
  const { prefix, query } = splitPrefix(input)
  if (!query || (prefix && prefix !== '?')) return []
  return sources.filter((source) => source.search && (!prefix || source.prefix === prefix))
}
