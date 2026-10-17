// 一条结果「还能做什么」。
//
// 回车做的那一件在最前，然后是它自己的几件（复制链接、归档、重命名……）。三个入口
// 升起的是同一份：桌面上按 Tab、右键一行，手机上长按一行（那一份从底部升起）。
import type { Ref } from 'vue'
import type { MenuCommand } from '@/commands'
import type { ResultRow } from './results'

import { ref } from 'vue'

import { t } from '@/i18n'

export interface PaletteActionsOptions {
  /** 现在列出来的所有行，按屏幕上的次序。 */
  rows: Ref<ResultRow[]>
  /** 高亮的是第几行。 */
  selected: Ref<number>
  /** 回车做的那件事。 */
  choose: (row: ResultRow, newTab?: boolean) => void
  /** 桌面上才有标签页可开，手机上这一栏不存在。 */
  mdAndUp: Ref<boolean>
}

export function usePaletteActions(options: PaletteActionsOptions) {
  const { rows, selected, choose, mdAndUp } = options

  /** 回车那一件叫什么，底栏上写的就是它。 */
  function verbOf(row: ResultRow): string {
    return row.verb ?? t(row.to ? 'navigation.palette.verbOpen' : 'navigation.palette.verbRun')
  }

  /** 一条结果能做的全部事：回车那一件在最前，然后是在新标签页打开，然后是它自己的。 */
  function actionsOf(row: ResultRow): MenuCommand[] {
    const list: MenuCommand[] = [{ id: 'palette.primary', title: verbOf(row), icon: row.icon, run: () => choose(row) }]
    // 手机上没有标签页可开。
    if (row.to && mdAndUp.value)
      list.push({
        id: 'palette.newTab',
        title: t('navigation.palette.newTab'),
        icon: 'mdi-open-in-new',
        run: () => choose(row, true),
      })
    if (row.scope)
      list.push({ id: 'palette.scope', title: t('navigation.palette.searchIn'), icon: 'mdi-magnify', run: () => {} })
    return [...list, ...(row.actions?.() ?? [])]
  }

  /** 只有回车那一件可做的（一条操作）不开清单。 */
  function hasMore(row: ResultRow | undefined): boolean {
    return !!row && actionsOf(row).length > 1
  }

  const acting = ref<{ row: ResultRow; actions: MenuCommand[] } | null>(null)
  const actSelected = ref(0)

  function openActions(row: ResultRow | undefined) {
    if (!row || !hasMore(row)) return
    acting.value = { row, actions: actionsOf(row) }
    actSelected.value = 0
  }

  // 右键一行：打开的就是 Tab 那一栏「还能做什么」，对的是右键的那一行。
  function onRowContextMenu(event: MouseEvent, index: number) {
    const row = rows.value[index]
    if (!hasMore(row)) return
    event.preventDefault()
    selected.value = index
    openActions(row)
  }

  // 手机上没有 Tab，长按一行（列表那边认出来的）升起同一份清单。
  const sheetOpen = ref(false)
  function openSheet(index: number) {
    const row = rows.value[index]
    if (!hasMore(row)) return
    selected.value = index
    acting.value = { row: row!, actions: actionsOf(row!) }
    sheetOpen.value = true
  }

  return { acting, actSelected, verbOf, actionsOf, hasMore, openActions, onRowContextMenu, openSheet, sheetOpen }
}
