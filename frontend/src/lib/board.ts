// 看板的呈现规则 —— **这里没有一行在算状态**。
//
// 一条活落哪一列、卡上写哪句话，全部由后端算好放在 `presentation` 里。前端这边只
// 留下和后端无关的三件事：列怎么排、列叫什么、同一列里谁排前面。
//
// 前端不再自己推一遍，是因为两个算法算同一个东西必然会走散，而屏幕上那个词到底是
// 哪一个算出来的，看的人分辨不了——他只会得出「界面在骗我」这一个结论。
//
// 列的判据是「**该谁动**」，不是「事情进行到哪一步」。这是整块板和一张表的区别：
// 表回答「有哪些活、它们各是什么状态」，板回答「现在轮到谁」。同一个客观事实——
// 比如快检红了——下一步在平台手上就落 `delivering`，在人手上就落 `needs_you`。

import type { BoardColumn, BoardPhrase } from '@/cx_types'

import i18n, { t } from '@/i18n'

export interface BoardColumnSpec {
  key: BoardColumn
  label: string
  /** Class suffix the host styles: `board-dot--building` 等。 */
  cls: string
}

// 列名按当前语言现取，不存成常量：切换语言后要跟着变。
export function columnLabel(column: BoardColumn): string {
  return t(`work.board.column.${column}`)
}

/** 卡面上那一句，按当前语言现取。后端给的是码（`presentation.phrase`）；这一版
 *  不认识的码（后端先发了新短语）照原样写出来，不写成空白。 */
export function phraseLabel(phrase: BoardPhrase): string {
  return i18n.global.te(`work.board.phrase.${phrase}`, 'zh-CN') ? t(`work.board.phrase.${phrase}`) : phrase
}

/** 色点的 class。列色是这套界面里唯一说「该谁动」的颜色，所以看板、房间总览、侧栏
 *  用的是同一个函数——三处对不上，看的人就得在脑子里做一次翻译。 */
export function columnDotClass(column: BoardColumn): string {
  return `board-dot--${column.replace('_', '-')}`
}

/** 色点长什么样。
 *
 *  它返回内联样式而不是让三个组件各写一遍 CSS：`scoped` 的样式进不了别的组件，所以
 *  「同一套列色」在 CSS 里只能靠复制三份来实现，而复制三份的意思就是迟早只改其中
 *  一份。同一个理由，颜色一律是 token 不是字面量——写死的颜色在两个主题里必然错
 *  一个。
 *
 *  只有 `needs_you` 是暖色且实心：整块板上唯一需要人动手的那一列，应该是唯一抓眼
 *  睛的。其余靠形状分（空心 / 虚线 / 实心），所以把颜色关掉也还读得出来。 */
export function columnDotStyle(column: BoardColumn): Record<string, string> {
  if (column === 'not_started') return { borderColor: 'var(--faint)', borderStyle: 'dashed' }
  if (column === 'building') return { borderColor: 'var(--ok)' }
  if (column === 'delivering') return { borderColor: 'var(--ok)', borderStyle: 'dashed' }
  if (column === 'needs_you') return { borderColor: 'var(--warn)', background: 'var(--warn)' }
  if (column === 'done') return { borderColor: 'var(--faint)', background: 'var(--faint)' }
  return { borderColor: 'var(--line-2)' }
}

/** 板上并排的那几列。
 *
 *  `done` 不在里面：它收进页面底部那条折叠行，板面留给还需要人看的东西。`archived`
 *  也不在：任务不归档（只有房间会），一个任务永远落不到那一列。 */
export const BOARD_COLUMNS: BoardColumnSpec[] = (['not_started', 'building', 'delivering', 'needs_you'] as const).map(
  (key) => ({
    key,
    get label() {
      return columnLabel(key)
    },
    cls: columnDotClass(key),
  })
)

/** 同一列里的先后。
 *
 *  新动过的排前面，**时间一样就比 id** —— 少了这条 tie-break，两条同秒更新的活谁
 *  在前面取决于数组原本的顺序，而那个顺序每次请求都可能不同，于是板会在两次刷新
 *  之间自己跳。排序必须是全序，不能只是「差不多有序」。 */
export interface SortableTask {
  id: string
  updated_at?: string | null
  created_at?: string | null
}

export function compareTasks(a: SortableTask, b: SortableTask): number {
  const at = Date.parse(a.updated_at ?? a.created_at ?? '')
  const bt = Date.parse(b.updated_at ?? b.created_at ?? '')
  const byTime = (Number.isNaN(bt) ? 0 : bt) - (Number.isNaN(at) ? 0 : at)
  if (byTime !== 0) return byTime
  return a.id.localeCompare(b.id)
}

/** 板上真正要看的那些活：去掉已归档房间里没走完的活。
 *
 *  活不归档，房间归档：一个房间收了尾，里面没走完的活（已退回、待回答）后端照样
 *  按它自己的状态落在未开始 / 进行中 / 检查中 / 待处理里，一挂就是几周。可房间归档
 *  了，就没人会再去动它们——四列答的是「接下来谁要动什么」，它们不该在上面。已完成不筛：
 *  那是交付过的东西，房间归档了也还是交付过。
 *
 *  看板和话题列表顶上那一行摘要都从这一份出发：两处数字对不上，看的人就不知道信
 *  哪个。 */
export function liveBoardTasks<T extends { room_id: string; presentation: { column: BoardColumn } }>(
  tasks: readonly T[],
  archivedRoomIds: ReadonlySet<string>
): T[] {
  return tasks.filter((task) => task.presentation.column === 'done' || !archivedRoomIds.has(task.room_id))
}

/** 板面三列各有几件，按「该你动」的那一列打头：摘要里第一眼要看到的是它。数为零
 *  的列不列。 */
export function boardColumnCounts(
  tasks: readonly { room_id: string; presentation: { column: BoardColumn } }[],
  archivedRoomIds: ReadonlySet<string>
): { key: BoardColumn; label: string; count: number }[] {
  const counts = new Map<BoardColumn, number>()
  for (const task of liveBoardTasks(tasks, archivedRoomIds)) {
    counts.set(task.presentation.column, (counts.get(task.presentation.column) ?? 0) + 1)
  }
  return (['needs_you', 'not_started', 'building', 'delivering'] as const)
    .map((key) => ({ key, label: columnLabel(key), count: counts.get(key) ?? 0 }))
    .filter((column) => column.count > 0)
}
