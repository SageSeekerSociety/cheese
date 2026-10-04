// BaseTable 和它的表头格子（BaseTableTh）之间的排序状态。表头是页面写进 `#head` 槽
// 的，所以状态走 provide / inject，不走 props。
import type { InjectionKey, Ref } from 'vue'

import { computed, ref, watch } from 'vue'

export type SortDir = 'asc' | 'desc'

export interface TableSortContext {
  key: Ref<string | null>
  dir: Ref<SortDir>
  toggle: (key: string) => void
}

export const TABLE_SORT: InjectionKey<TableSortContext> = Symbol('table-sort')

/**
 * 页面自己排序时用的比较：数字按大小，字符串按本地化顺序，空值永远排在最后
 * （不管升序降序——「没有」不是最小也不是最大）。
 */
export function compareBy<T>(
  rows: readonly T[],
  key: string | null,
  dir: SortDir,
  pick = (row: T, k: string) => (row as Record<string, unknown>)[k]
): T[] {
  if (!key) return [...rows]
  const sign = dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const x = pick(a, key)
    const y = pick(b, key)
    const xEmpty = x === null || x === undefined || x === ''
    const yEmpty = y === null || y === undefined || y === ''
    if (xEmpty || yEmpty) return xEmpty === yEmpty ? 0 : xEmpty ? 1 : -1
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * sign
    return String(x).localeCompare(String(y)) * sign
  })
}

/**
 * 整张表都在手上时（数据一次读回来）的排序 + 分页：替代 v-data-table 自带的那一套。
 * 换了排序回到第一页；数据变了而当前页超出范围时退回最后一页。
 */
export function useClientTable<T>(
  rows: Ref<readonly T[]>,
  opts: { perPage?: number; pick?: (row: T, key: string) => unknown } = {}
) {
  const perPage = opts.perPage ?? 10
  const sortKey = ref<string | null>(null)
  const sortDir = ref<SortDir>('asc')
  const page = ref(1)

  const sorted = computed(() => compareBy(rows.value, sortKey.value, sortDir.value, opts.pick))
  const pageRows = computed(() => sorted.value.slice((page.value - 1) * perPage, page.value * perPage))

  watch(
    () => rows.value.length,
    (n) => {
      const last = Math.max(1, Math.ceil(n / perPage))
      if (page.value > last) page.value = last
    }
  )

  function onSort(key: string, dir: SortDir) {
    sortKey.value = key
    sortDir.value = dir
    page.value = 1
  }

  return { sortKey, sortDir, page, perPage, pageRows, onSort }
}
