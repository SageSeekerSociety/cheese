// 看板各屏共用的**形状与纯函数**。
//
// 拆出来的理由和 `lib/navTarget.ts` 是同一条：`components/admin/dashboard/*.vue` 不许
// 碰 `@/api`（`boundary/no-api-or-router` 按**解析后的路径**判，连类型导入也算一次违
// 规），而每一屏的 props 都得说清自己吃的是哪一份响应。于是类型从这里走一道 —— 下面
// 那几个类型再导出只活在类型里，构建期就被抹掉，运行时不拉一个字节的取数代码，所以各
// 屏仍然是「只吃 props」的那种组件（`frontend_grade.py` 的 A 级）。
//
// 这里只放**算一次、屏屏都用**的东西：数字串、环比、轴标签、队列地址、KPI 行的形状。
// 每一屏自己的拆分（哪几张卡、哪些段）留在那一屏里 —— 那些判据属于那一屏的读法。
import type { Composer } from 'vue-i18n'
import type { RouteLocationRaw } from 'vue-router'
import type { StatsDays, StatsKind } from '@/api'
import type { FeedbackStatus } from '@/cx_types'

import { fmtDelta, fmtNum } from './usageFormat'

export type {
  StatsDays,
  StatsFeedback,
  StatsIntegrations,
  StatsKind,
  StatsPerformance,
  StatsPipeline,
  StatsPlatform,
  StatsProduct,
  StatsUsage,
} from '@/api'

/** 队列的地址。写**地址**不写路由名：规格 §11 第 9 条钉的是地址。 */
const QUEUE = '/admin/queue'

export const queue = (query: Record<string, string> = {}): RouteLocationRaw => ({ path: QUEUE, query })

/** 数字串。拿不到来源（`null`）时给空串，卡片自己画成 `—`；给 0 的话「没读到」和
 *  「读出来确实是零」在屏幕上就分不开。 */
export const num = (v: number | null | undefined): string => (v === null || v === undefined ? '' : fmtNum(v))

/** KPI 卡一行的完整形状（模板按这个形状传参，省的每行猜有哪些键）。 */
export interface KpiRow {
  key: string
  label: string
  value: string
  loading: boolean
  to?: RouteLocationRaw
  delta?: string
  deltaTitle?: string
  spark?: (number | null)[]
  note?: string
}

/** 「需处理」迷你列表的一行。取数那一半从 store 的队列里切出来，画法在
 *  `AdminNumberList`。 */
export interface PendingRow {
  id: string
  no: number
  title: string
  status: FeedbackStatus
  updatedAt: string
}

/** 各屏和取数那一半手里的 `t`。**不写死成 `(key, named) => string`**：i18n 的 `t`
 *  是一组重载，写窄了 `useI18n()` 那个 `t` 传不进来。 */
export type Trans = Composer['t']

/** delta 与其口径句（挂 title 的「上一周期（再前 {d} 天）：{v}」）。
 *  `prev` 缺字段（旧后端）→ 两个都不给，卡上不出现 delta；`prev = 0` → `fmtDelta`
 *  给空串（不画「+∞%」这种鬼话）。`prevText` 是已格式化的 prev 全值（fmtNum / fmtCost）。
 *
 *  `t` 与 `days` 是**调用点传进来的头两个参数**：在原页面上这两样是闭包里的自由变量
 *  （`useI18n()` 的 `t` 和 `store.statsDays`），各屏搬走以后没有那个作用域了。 */
export function deltaOf(
  t: Trans,
  days: StatsDays,
  cur: number | null | undefined,
  prev: number | null | undefined,
  prevText: string
) {
  if (prev === null || prev === undefined) return { delta: undefined, deltaTitle: undefined }
  return {
    delta: fmtDelta(cur, prev),
    deltaTitle: t('feedback.dashboard.kpi.vsPrev', { d: days, v: prevText }),
  }
}

/** 轴标签写「9/15」：轴上七个点，写全年月日是七串数字挤在一起，而窗口在页头上已经
 *  说了是几天。 */
export function dayLabel(date: string): string {
  // 后端给的是 `YYYY-MM-DD`（或带时间的 ISO 串），取前两段就够，别交给 `Date` 去解析
  // —— 那会按本地时区把日期挪一天。
  const [, month, day] = date.slice(0, 10).split('-')
  return `${Number(month)}/${Number(day)}`
}
