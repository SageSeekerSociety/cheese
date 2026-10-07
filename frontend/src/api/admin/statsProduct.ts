import type { StatsFeedback, StatsPerformance, StatsPlatform, StatsUsage } from './stats'

import { request } from '../http'
import { feedbackQuery } from '../query'

/** 分类 → 它那条接口的形状。`getStats` 的返回类型由这个映射查出来，所以调用方
 *  拿到的永远是它问的那一类，而不是一个四选一的联合（联合要在每个用的地方再窄化
 *  一次，而那正是「切到用量页却读了反馈的字段」这类错会藏身的地方）。 */
/** 交付管线那一块。口径的三条硬事实写在 `domain/platform_stats/pipeline.py`：
 *  机器闸门已退役（`pending_gate`/`gate_failed`/`gate_blocked`/`pr_open` 是死写入）、
 *  `void` 不是一个状态（它是 `revoked` + `note_code=voided`）、`decided_at` 会被
 *  revoke 覆写。页面上的注脚对应的就是它们。 */
export interface StatsPipeline {
  days: number
  backlog: {
    /** 每一档都在，**包括死写入的那几档**：缺档和 0 在屏幕上必须长得不一样。 */
    by_status: Record<string, number>
    /** `note_code ∈ _STUCK` 的卡。判据是码，不是文案。 */
    stuck: number
    /** 非终态的卡 —— 它们会堵死整间房的重新递卡。 */
    blocking_refile: number
    /** 人工作废：`revoked` 里带 `note_code=voided` 的那部分。 */
    voided_stock: number
    live_total: number
    settled_total: number
  }
  stuck_cards: {
    card_id: string
    topic_id: string
    topic_title: string
    project_id: string
    task_id: string | null
    reviewer_handle: string
    status: string
    note_code: string | null
    note: string
    change_subject: string | null
    age_seconds: number
  }[]
  dwell: {
    filed_to_decision: {
      count: number
      p50_seconds: number | null
      p90_seconds: number | null
      max_seconds: number | null
    }
    filed_to_merge: {
      count: number
      p50_seconds: number | null
      p90_seconds: number | null
      max_seconds: number | null
    }
    /** 在途卡的年龄。未决议的卡是右删失样本，不进上面的百分位。 */
    open_card_age: {
      count: number
      p50_seconds: number | null
      max_seconds: number | null
    }
    accepted_not_archived: { count: number; max_seconds: number | null }
  }
  needs_you: {
    reviewer_pending: number
    open_tasks: number
    awaiting_answer: number
    /** 判据是 `delivery/addressing.py` 的三个码，不发明第四个。 */
    reasons: { reviewer: number; reporter: number; asked: number }
    items: {
      kind: string
      id: string
      title: string
      project_id: string
      topic_id: string
      at: string | null
    }[]
  }
  turn_failures: {
    /** 结构化来源是 `blocks.meta`，**不是** `agent_turns`（那张表没有失败列）。 */
    by_code: Record<string, number>
    other: number
    /** `credits_refused_at` 是唯一可靠的额度拒答来源。 */
    credits_refused: number
    prompt_undelivered: number
  }
  host_health: {
    /** 只保留**当前** streak —— 成功一次就删行，答不了「上周隔离过几台」。 */
    tracked: number
    quarantined: number
    rows: {
      device_id: string
      consecutive_failures: number
      last_failure_code: string | null
      last_failure_at: string | null
      quarantined_until: string | null
    }[]
  }
  unsettled_dispatches: number
}

/** 产品健康：北极星 + 护栏。`unavailable` 里那两条**今天根本算不出来**。 */
export interface StatsProduct {
  days: number
  north_star: {
    /** 按 `decided_at` 分桶、只数**现在**仍是 accepted 的卡 —— 窗口口径，
     *  和 series、和卡片标签同一把尺子。 */
    total: number
    /** 上一等长窗口的同一口径合计（环比用）。可选：旧后端还没有它。 */
    prev_total?: number
    series: { date: string; accepted: number }[]
    note_key: string
  }
  rejection: {
    filed: number
    returned: number
    /** 打回率。分母是窗口内**创建**的卡（含还在走的）。 */
    returned_rate: number | null
    buckets: {
      accepted: number
      rejected: number
      voided: number
      /** `revoked` 里**不带** `voided` 码的那部分（采纳后撤销 / 归档扫尾）。 */
      revoked_after_accept: number
      revoked_other: number
      gate_failed: number
      gate_blocked: number
      conflict: number
      live: number
      pr_open: number
    }
    note_key: string
  }
  unavailable: { name: string; reason_key: string; needs: string }[]
}

/** 集成与凭据：静默降级。**没有 `days`** —— 存量问题，不是窗口曲线。 */
export interface StatsIntegrations {
  oauth: {
    total: number
    expired: number
    expiring_7d: number
    /** 和「过期」不是一件事：没有 refresh 的到期那天就永远接不上了。 */
    no_refresh_token: number
    note_key: string
  }
  passkey: {
    accounts: number
    with_passkey: number
    coverage: number | null
    note_key: string
  }
  delivery: {
    /** 还会补发的（`attempts < MAX`）和已放弃的死信，**两档必须分开**。 */
    unsent: number
    dead_letters: number
    oldest_unsent_at: string | null
    max_attempts: number
    note_key: string
  }
  unavailable: { name: string; reason_key: string; needs: string }[]
}

export interface StatsShapes {
  feedback: StatsFeedback
  usage: StatsUsage
  platform: StatsPlatform
  performance: StatsPerformance
  pipeline: StatsPipeline
  product: StatsProduct
  integrations: StatsIntegrations
}
export type StatsKind = keyof StatsShapes

/** 看板的统计窗口。三档而不是任意整数：页头切换器只有三个位置，而「窗口」这一档
 *  该进 store（切窗口重拉已加载的类），不该在每个调用点各自传一个字面量。 */
export type StatsDays = 7 | 30 | 90

export function getStats<K extends StatsKind>(kind: K, opts?: { days?: StatsDays }): Promise<StatsShapes[K]> {
  return request<StatsShapes[K]>(`/admin/stats/${kind}${feedbackQuery({ days: opts?.days ?? 7 })}`)
}
