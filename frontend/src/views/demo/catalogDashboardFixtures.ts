/**
 * 看板那八件（`components/admin/dashboard/*.vue`）在预览站里吃的数据。
 *
 * 形状**不是编的**：`/admin/stats/*` 那七条响应按 `Stats*`（`@/lib/adminStats`）标
 * 了类型，少一个键、多一个键都在 `vue-tsc` 那里当场红；值也照抄预览站假后端
 * （`proto-feedback-fixtures.ts`）里那七台函数算出来的东西，只把长列表截短
 * （三十天的 series 留七个点、路由表留三条）。
 *
 * 为什么单独一份文件：`catalogFixtures.ts` 已经六百多行，七份响应塞进去会顶到
 * `frontend/src` 那一千行的上限；和 `catalogRail.ts` 同一个理由（见那边开头）。
 *
 * 为什么这八件能在预览站里单独画：它们是「只吃 props」的那种组件（`frontend_grade.py`
 * 的 A 级），取数全在 `composables/useAdminStats.ts` 里 —— 所以这里给的就是一份
 * 现成的 props，不用起假后端。
 */
import type {
  PendingRow,
  StatsDays,
  StatsFeedback,
  StatsIntegrations,
  StatsKind,
  StatsPerformance,
  StatsPipeline,
  StatsPlatform,
  StatsProduct,
  StatsUsage,
} from '@/lib/adminStats'

/** 七个点的日期轴（`dayLabel` 把它写成「9/24」）。 */
const DAYS: string[] = [
  '2026-09-24',
  '2026-09-25',
  '2026-09-26',
  '2026-09-27',
  '2026-09-28',
  '2026-09-29',
  '2026-09-30',
]

/** 逐日的七个值（和 `DAYS` 一一对应）：`date` 加一个数量键。返回的就是那一行的形状，
 *  多的键由调用方 `.map` 补上 —— 这样八份读数和 `Stats*` 是一处一处对上的，不是先
 *  装进 `Record<string, unknown>` 再断言回去。里面那一个 `as` 是因为 TS 证不出**计算
 *  属性名**对应的值类型；错都错在调用方的注解上（多一个键、少一个键那里当场红）。 */
const week = <K extends string>(values: number[], key: K): ({ date: string } & Record<K, number>)[] =>
  DAYS.map((date, i) => ({ date, [key]: values[i] ?? 0 })) as ({ date: string } & Record<K, number>)[]

// --- 页头 ------------------------------------------------------------------

/** 页头那一格的整串 props（换的只是 `current` / `days`）。 */
export function dashHeaderProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    windowed: true,
    days: 30,
    stamp: '06:13',
    ...over,
  }
}

/** 页头上的窗口切换器：7 / 30 / 90。 */
export const DASH_DAYS: StatsDays[] = [7, 30, 90]

// --- 七份响应 --------------------------------------------------------------

export const DASH_PIPELINE: StatsPipeline = {
  days: 30,
  backlog: {
    by_status: {
      pending: 4,
      pending_gate: 0,
      conflict: 1,
      accepted: 12,
      rejected: 2,
      revoked: 3,
      gate_failed: 0,
      gate_blocked: 0,
      pr_open: 0,
    },
    stuck: 2,
    blocking_refile: 5,
    voided_stock: 1,
    live_total: 5,
    settled_total: 17,
  },
  stuck_cards: [
    {
      card_id: 'c1',
      topic_id: 't1',
      topic_title: '官改文档 的成果待验收',
      project_id: 'p1',
      task_id: null,
      reviewer_handle: 'wangchangxin',
      status: 'pending',
      note_code: 'repush_failed',
      note: '修复没能推上 GitHub',
      change_subject: 'docs: rewrite the handover doc',
      age_seconds: 7200,
    },
    {
      card_id: 'c2',
      topic_id: 't2',
      topic_title: '容器 的 PR 卡住了',
      project_id: 'p2',
      task_id: null,
      reviewer_handle: 'wangchangxin',
      status: 'pending',
      note_code: 'merge_conflict',
      note: '合并冲突，已派芝士解决',
      change_subject: 'feat: add sandbox census',
      age_seconds: 93600,
    },
  ],
  dwell: {
    filed_to_decision: { count: 9, p50_seconds: 14400, p90_seconds: 108000, max_seconds: 180000 },
    filed_to_merge: { count: 6, p50_seconds: 7560, p90_seconds: 68400, max_seconds: 108000 },
    open_card_age: { count: 5, p50_seconds: 21600, max_seconds: 93600 },
    accepted_not_archived: { count: 2, max_seconds: 43200 },
  },
  needs_you: {
    reviewer_pending: 4,
    open_tasks: 3,
    awaiting_answer: 1,
    reasons: { reviewer: 4, reporter: 3, asked: 1 },
    items: [
      {
        kind: 'room',
        id: 't1',
        title: '官改文档 的成果待验收',
        project_id: 'p1',
        topic_id: 't1',
        at: '2026-09-30T00:13:49.870Z',
      },
    ],
  },
  turn_failures: {
    by_code: {
      turn_timeout: 1,
      prompt_undelivered: 1,
      host_unreachable: 0,
      storage_exhausted: 0,
      runtime_image_missing: 0,
      subscription_credential_expired: 1,
      workspace_vcs_perms: 0,
    },
    other: 0,
    credits_refused: 1,
    prompt_undelivered: 1,
  },
  host_health: {
    tracked: 1,
    quarantined: 0,
    rows: [
      {
        device_id: 'dev-3',
        consecutive_failures: 2,
        last_failure_code: 'storage_exhausted',
        last_failure_at: '2026-09-30T04:13:49.870Z',
        quarantined_until: null,
      },
    ],
  },
  unsettled_dispatches: 0,
}

export const DASH_PRODUCT: StatsProduct = {
  days: 30,
  north_star: {
    total: 38,
    prev_total: 31,
    series: week([2, 3, 5, 4, 6, 8, 10], 'accepted'),
    note_key: 'product.northStarNote',
  },
  rejection: {
    filed: 10,
    returned: 3,
    returned_rate: 0.3,
    buckets: {
      accepted: 5,
      rejected: 1,
      voided: 1,
      revoked_after_accept: 1,
      revoked_other: 0,
      gate_failed: 1,
      gate_blocked: 0,
      conflict: 0,
      live: 1,
      pr_open: 0,
    },
    note_key: 'product.rejectionNote',
  },
  unavailable: [
    {
      name: 'acceptance_rate_after_summon',
      reason_key: 'product.unavailable.summon',
      needs: 'agent_turns.summon 落库，accept_cards 带上来源轮次',
    },
    {
      name: 'churn_after_credits_exhausted',
      reason_key: 'product.unavailable.churn',
      needs: 'compute_grants.exhausted_at + 账号活跃心跳',
    },
  ],
}

export const DASH_INTEGRATIONS: StatsIntegrations = {
  oauth: { total: 40, expired: 3, expiring_7d: 2, no_refresh_token: 5, note_key: 'integrations.oauthNote' },
  passkey: { accounts: 120, with_passkey: 36, coverage: 0.3, note_key: 'integrations.passkeyNote' },
  delivery: {
    unsent: 2,
    dead_letters: 1,
    oldest_unsent_at: '2026-09-30T05:13:49.874Z',
    max_attempts: 5,
    note_key: 'integrations.deliveryNote',
  },
  unavailable: [
    {
      name: 'github_app_permission_gaps',
      reason_key: 'integrations.unavailable.githubPerms',
      needs: '遍历 project_git_installations 调 granted_permissions() 并落库',
    },
    {
      name: 'github_app_mint_failure_rate',
      reason_key: 'integrations.unavailable.githubMint',
      needs: '在 GitHubAppTokens._mint 失败处计数',
    },
    {
      name: 'login_lockout_stock_and_rate',
      reason_key: 'integrations.unavailable.lockout',
      needs: '登录限流/锁定事件落库',
    },
    {
      name: 'metering_post_freeze',
      reason_key: 'integrations.unavailable.metering',
      needs: '计量代理落账的心跳或对账落库',
    },
  ],
}

export const DASH_FEEDBACK: StatsFeedback = {
  days: 30,
  total: { all: 50, open: 35, closed: 15, unassigned: 19, urgent_open: 9 },
  columns: { public: 42, private: 4, agent: 6, security: 4 },
  status: { received: 24, in_progress: 11, resolved: 8, deployed: 7, declined: 2 },
  unread: 12,
  series: week([3, 5, 2, 6, 4, 7, 3], 'created').map((row, i) => ({
    ...row,
    resolved: [2, 4, 3, 5, 3, 6, 2][i],
    deployed: [1, 1, 2, 3, 2, 4, 1][i],
  })),
  prev: { created: 1, resolved: 1 },
}

/** 「需处理」那十行（不在看板接口里，页面从队列那一路切出来的）。 */
export const DASH_PENDING: PendingRow[] = [
  {
    id: 'f-1042',
    no: 1042,
    title: '导出一个月的数据要等四十秒',
    status: 'received',
    updatedAt: '2026-09-30T05:02:00Z',
  },
  {
    id: 'f-1039',
    no: 1039,
    title: '看板上「递卡受阻」点不动',
    status: 'in_progress',
    updatedAt: '2026-09-30T03:41:00Z',
  },
  {
    id: 'f-1031',
    no: 1031,
    title: '手机上阶段条会把当前那一格挤出视野',
    status: 'received',
    updatedAt: '2026-09-29T22:10:00Z',
  },
]

/** 账号池快照写下的时刻。**算出来而不是写死**：这是一份静态素材，写死的时刻过几天
 *  就变成一个过去的钟点，预览里那一句「最早 X 恢复」跟着变成一句假话。 */
const POOL_AT = Math.floor(Date.now() / 1000)

export const DASH_USAGE: StatsUsage = {
  days: 30,
  totals: { tokens: 921100, calls: 277, cost_usd: 2.6583, unpriced_tokens: 35000 },
  series: week([45500, 61200, 38000, 74100, 50800, 92000, 63000], 'tokens').map((row, i) => ({
    ...row,
    calls: [13, 18, 11, 22, 15, 27, 19][i],
    cost_usd: [0.1296, 0.1742, 0.1081, 0.214, 0.145, 0.2603, 0.1806][i],
  })),
  top_projects: [
    { project_id: '4a1c0f6e-7b52-4d9a-9c31-2f8d5a0b7e11', name: '知是平台后端', tokens: 221100, cost_usd: 0.6381 },
    { project_id: 'p-y', name: '城西社区', tokens: 118400, cost_usd: 0.3412 },
  ],
  by_model: [
    { model: 'glm-4.6', tokens: 423700, calls: 141, cost_usd: 1.1976, unpriced_tokens: 24500 },
    { model: 'deepseek-v3', tokens: 197400, calls: 66, cost_usd: 0.5591, unpriced_tokens: 10500 },
  ],
  by_route: [
    { route: 'gateway', tokens: 571100, calls: 190, cost_usd: 1.7133, unpriced_tokens: 0 },
    { route: 'runtime', tokens: 210000, calls: 61, cost_usd: 0.61, unpriced_tokens: 35000 },
  ],
  prev: { tokens: 1096100, calls: 308, cost_usd: 3.1527 },
  credits: {
    exhausted: [
      {
        project_id: 'p-x',
        name: '容器',
        credits_total: 100,
        credits_used: 110,
        credits_remaining: -10,
        ratio: 1,
      },
    ],
    low: [
      {
        project_id: 'p-y',
        name: '城西社区',
        credits_total: 200,
        credits_used: 190,
        credits_remaining: 10,
        ratio: 0.05,
      },
    ],
    unlimited_project_ids: ['p-z'],
    unlimited_count: 1,
    burn: {
      credits_in_window: 42,
      credits_per_day: 6,
      method: 'derived_from_resource_usage',
    },
  },
  // Claude 账号池：三张账号各一种状态 —— 这一块要一眼看得出「一行一张账号」在画什么，
  // 所以可用 / 冷却 / 已停用各来一张，冷却那张还带着它自己的失败次数。
  claude_accounts: {
    accounts: [
      { name: 'primary', state: 'available', until: null, failures: 0 },
      { name: 'claude-2', state: 'cooling', until: POOL_AT + 900, failures: 2 },
      { name: 'claude-3', state: 'disabled', until: null, failures: 6 },
    ],
    reason: null,
    written_at: POOL_AT,
    age_seconds: 0,
    stale: false,
    retry_after: 900,
  },
}

export const DASH_PLATFORM: StatsPlatform = {
  days: 30,
  people: {
    total: 1199,
    new: 37,
    prev_new: 41,
    admins: 3,
    humans: 1187,
    agents: 12,
    new_humans: 26,
    new_agents: 11,
    series: week([1, 4, 2, 6, 3, 8, 5], 'created').map((row, i) => ({
      ...row,
      human_created: [1, 3, 2, 5, 3, 7, 4][i],
      agent_created: [0, 1, 0, 1, 0, 1, 1][i],
    })),
  },
  machines: { devices: 7, hosted_devices: 5, warm_machines: 3, cloud_hosts: 2 },
  health: {
    overall: 'healthy',
    checks: {
      database: { status: 'up' },
      redis: { status: 'up' },
      event_loop: { status: 'up', detail: 3.4 },
      routes: { status: 'up' },
    },
  },
  extras: {
    disk: {
      available: true,
      free_gb: 274.9,
      total_gb: 566.8,
      used_pct: 51.5,
      tier: 'ok',
      warn_pct: 85,
      critical_pct: 95,
      note_key: 'platform.diskNote',
    },
    preview: { available: true, attached: 1, note_key: 'platform.previewNote' },
    machines: {
      devices: 7,
      hosted_devices: 5,
      warm_total: 3,
      warm_by_state: { ready: 2, preparing: 1 },
      warm_error: 0,
      host_total: 2,
      host_by_status: { running: 1, deleted: 1 },
      host_active: 1,
      host_enroll_error: 0,
      host_slots_used: 3,
      host_slots_total: 8,
      note_key: 'platform.machinesNote',
    },
  },
}

export const DASH_PERFORMANCE: StatsPerformance = {
  routes_registered: 562,
  routes_with_samples: 5,
  routes_omitted: 0,
  dropped_series: 0,
  routes: [
    {
      method: 'GET',
      route: '/feedback',
      count: 412,
      error_count: 2,
      status: { '2xx': 405, '3xx': 5, '4xx': 2, '5xx': 0 },
      p50: 18.4,
      p95: 61.2,
      p99: 143.8,
      spark: [12, 18, 15, 22, 19, 26, 21, 17, 24, 20, 16, 23],
    },
    {
      method: 'POST',
      route: '/feedback/{id}/comments',
      count: 88,
      error_count: 0,
      status: { '2xx': 88, '3xx': 0, '4xx': 0, '5xx': 0 },
      p50: 24.1,
      p95: 112.6,
      p99: 208.4,
      spark: [22, 26, 24, 31, 28, 35, 30, 27, 33, 29, 25, 32],
    },
    {
      method: 'GET',
      route: '/admin/stats/performance',
      count: 31,
      error_count: 1,
      status: { '2xx': 30, '3xx': 0, '4xx': 1, '5xx': 0 },
      p50: 41.7,
      p95: 188.3,
      p99: 260.9,
      spark: [38, 44, 41, 52, 47, 61, 55, 49, 58, 51, 46, 57],
    },
  ],
  network: {
    uplink: {
      available: true,
      iface: 'eth0',
      scope: 'host',
      rx_bps: 184320.5,
      tx_bps: 2411724.8,
      samples: [
        { rx_bps: 120000, tx_bps: 1800000 },
        { rx_bps: 150000, tx_bps: 2100000 },
      ],
      note_key: 'perf.netHost',
    },
    api: {
      available: true,
      iface: null,
      scope: 'process',
      rx_bps: 184320.5,
      tx_bps: 2411724.8,
      samples: [
        { rx_bps: 120000, tx_bps: 1800000 },
        { rx_bps: 150000, tx_bps: 2100000 },
      ],
      note_key: 'perf.apiIO',
    },
  },
  active_requests: 2,
  uptime_seconds: 20220,
  loop_lag: { recent_ms: 3.4, worst_ms: 182.6 },
  reliability: { delivery_unsent: 2, delivery_dead_letters: 1 },
}
