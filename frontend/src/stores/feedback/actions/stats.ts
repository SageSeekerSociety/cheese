/**
 * 看板：管理台那一页的三个汇总窗口。
 *
 * 每**一类**各存各的一份（`state.stats[kind]`）、各有一把代次和一面「在加载中」的
 * 旗（`session.statsSeq` / `state.statsBusy`）。切到哪一类才拉哪一类，切回来不再拉
 * 一次；共用一把计数器的写法在这里是错的 —— 见 `session.statsSeq` 上那段。
 */

import type { StatsDays, StatsKind } from '@/api'
import type { FeedbackState } from '../state'

import { message, WINDOWED_KINDS } from '../helpers'
import { session } from '../session'
import { assignStats } from '../state'

import { getStats } from '@/api'
import { t } from '@/i18n'

interface StatsSelf {
  stats: FeedbackState['stats']
  statsKind: StatsKind
  statsDays: StatsDays
  statsAt: FeedbackState['statsAt']
  statsBusy: FeedbackState['statsBusy']
  error: string | null
  /** 切窗口之后重拉那几类 —— 定义在同组的 `loadStats`。 */
  loadStats(kind?: StatsKind, days?: StatsDays): Promise<void>
}

export const statsActions = {
  /* ---- 看板 ---- */

  /** 拉看板的**一个分类**。`kind` 缺省是当前停着的那一类，`days` 缺省读
   *  `this.statsDays`（**不再硬默认 7**）—— 窗口是这一页的状态，R 键那一路
   *  `loadStats()` 不传参数，在 30 天窗口下也不该把数据偷偷拉回 7 天。
   *
   *  **切分类要把当时那一类钉住**：请求发出去之后人才切的分类，回来的那一份是给上一个
   *  分类的，写进 `stats[kind]` 才对；写进「现在这一类」就成了「切到用量、画出来的是
   *  反馈的数」。所以 `kind` 在这里被捕获，不读 `this.statsKind`。
   *
   *  和列表一样要扔掉过期响应：R 键连按两次会发两个请求，而先发的那次可能后到。 */
  async loadStats(this: StatsSelf, kind?: StatsKind, days?: StatsDays): Promise<void> {
    // `kind` 缺省是「当前停着的那一类」，但**在这里定下来**（不写进参数默认值：
    // 参数默认值里的 `this` 在 options store 里没有类型，而它读的正是 this）。
    const wanted: StatsKind = kind ?? this.statsKind
    const daysToUse = days ?? this.statsDays
    const seq = ++session.statsSeq[wanted]
    this.statsBusy[wanted] = true
    this.error = null
    try {
      const stats = await getStats(wanted, { days: daysToUse })
      if (seq !== session.statsSeq[wanted]) return
      assignStats(this.stats, wanted, stats)
      // 时间戳只记**成功**那一趟：失败留着旧数据就配旧时刻（见 state 上的注释）。
      this.statsAt[wanted] = Date.now()
    } catch (error) {
      if (seq !== session.statsSeq[wanted]) return
      this.error = message(error, t('feedback.errors.dashboardLoadFailed'))
    } finally {
      if (seq === session.statsSeq[wanted]) this.statsBusy[wanted] = false
    }
  },

  /** 切统计窗口（7/30/90）：**缓存键就是窗口** —— `stats[kind]` 只存一份，不保留
   *  多窗口副本（保留的话，导轨短值会混窗口：用量卡显示 30 天的、反馈卡显示 7 天的）。
   *  所以切窗口 = 把**已加载的窗口类**全部重拉一遍；还没加载的类等切过去时按新窗口拉。
   *
   *  `performance` / `integrations` 没有窗口语义，跳过重拉。每类各自的并发由 per-kind
   *  `session.statsSeq` / `statsBusy` 守卫覆盖：重拉期间当前类进骨架，其余类的旧数留到新数到货。 */
  setStatsDays(this: StatsSelf, days: StatsDays) {
    if (days === this.statsDays) return
    this.statsDays = days
    for (const kind of WINDOWED_KINDS) {
      if (this.stats[kind] !== null) void this.loadStats(kind, days)
    }
  },
}
