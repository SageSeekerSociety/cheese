/**
 * proto-feedback-fixtures/statsRoutes.ts — 看板里四类**形状逐字照抄后端**的构造器：耗时
 * （performance）、交付管线（pipeline）、产品健康（product）、集成与凭据（integrations），
 * 一份对 `domain/platform_stats/` 下的同名模块。
 *
 * 从 `routes.ts` 搬出来的：这四个加起来三百多行，只被 `/admin/stats/*` 那一条分发用到，
 * 留在路由表里把「一条路径进来、回什么」这条主线埋掉了。搬出来只挪了位置 —— 窗口天数
 * 仍从 `./stats` 取，返回的形状一个字节都没动。
 */
import { readDays, windowDays } from './stats'

/** 第四类：**这一刻**的接口耗时。
 *
 *  **这一块在真后端上读的不是库**：`domain/platform_stats/performance.py` 直接读
 *  `core/metrics.py` 那个进程内的注册表 —— 所以预览里只能编，而且必须编得像
 *  「这个进程刚被访问过」。形状逐字照抄那个服务。
 *
 *  两处是刻意留的：
 *
 *   * **列表是按 p95 从大到小排的**，因为真服务就是这么排的（`routes.sort(...)`）——
 *     预览里顺手按别的顺序摆，会让人以为页面的排序是页面自己做的。
 *   * **留一条 `p95: null` 的路由**：没有样本的分位数是 `null`、页面画「—」。不留
 *     一条的话，「没有数据」和「0 毫秒」在预览里长得一模一样，而那一版正是最该被
 *     看见的一版。
 */
export function performanceStats(): Record<string, unknown> {
  // 形状逐字照抄 `domain/platform_stats/performance.py`：**每一条注册过的端点一行**，
  // 没被访问过的也占一行（`count: 0`、分位数 null）。状态码是属性不是身份。
  const spark = [12, 14, 11, 18, 22, 19, 16, 15, 17, 21, 24, 20, 18, 17, 16, 15, 14, 16, 18, 19, 21, 23, 22, 20]
  const routes = [
    {
      method: 'GET',
      route: '/feedback',
      count: 412,
      error_count: 2,
      status: { '2xx': 405, '3xx': 5, '4xx': 2, '5xx': 0 },
      p50: 18.4,
      p95: 61.2,
      p99: 143.8,
      spark,
    },
    {
      method: 'GET',
      route: '/projects',
      count: 188,
      error_count: 0,
      status: { '2xx': 188, '3xx': 0, '4xx': 0, '5xx': 0 },
      p50: 22.1,
      p95: 48.9,
      p99: 96.4,
      spark,
    },
    {
      method: 'GET',
      route: '/feedback/{feedback_id}',
      count: 96,
      error_count: 1,
      status: { '2xx': 95, '3xx': 0, '4xx': 1, '5xx': 0 },
      p50: 12.7,
      p95: 33.5,
      p99: 71.2,
      spark,
    },
    {
      method: 'POST',
      route: '/topics/{topic_id}/messages',
      count: 54,
      error_count: 0,
      status: { '2xx': 54, '3xx': 0, '4xx': 0, '5xx': 0 },
      p50: 41.3,
      p95: 122.6,
      p99: 251.9,
      spark,
    },
    {
      method: 'GET',
      route: '/admin/stats/feedback',
      count: 31,
      error_count: 0,
      status: { '2xx': 31, '3xx': 0, '4xx': 0, '5xx': 0 },
      p50: 9.8,
      p95: 24.1,
      p99: 38.7,
      spark,
    },
    // 刚加过路由、还没人访问过的那几条：分位数是 null，页面画「—」。
    {
      method: 'GET',
      route: '/spaces/{space_id}/domain-groups',
      count: 0,
      error_count: 0,
      status: { '2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0 },
      p50: null,
      p95: null,
      p99: null,
      spark: [],
    },
    {
      method: 'DELETE',
      route: '/admin/admins/{handle}',
      count: 0,
      error_count: 0,
      status: { '2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0 },
      p50: null,
      p95: null,
      p99: null,
      spark: [],
    },
  ]
  const net = {
    available: true,
    iface: 'eth0',
    scope: 'host',
    rx_bps: 184_320.5,
    tx_bps: 2_411_724.8,
    samples: Array.from({ length: 24 }, (_, i) => ({
      rx_bps: 120_000 + i * 4000,
      tx_bps: 1_800_000 + i * 22_000,
    })),
    note_key: 'perf.netHost',
  }
  return {
    routes_registered: 562,
    routes_with_samples: routes.filter((r) => r.count > 0).length,
    routes_omitted: 0,
    dropped_series: 0,
    routes,
    network: {
      uplink: net,
      api: { ...net, iface: null, scope: 'process', note_key: 'perf.apiIO' },
    },
    active_requests: 2,
    uptime_seconds: 5 * 3600 + 37 * 60,
    loop_lag: { recent_ms: 3.4, worst_ms: 182.6 },
    reliability: {
      delivery_unsent: 2,
      delivery_dead_letters: 1,
    },
  }
}

/** 交付管线。形状逐字照抄 `domain/platform_stats/pipeline.py`。
 *
 *  刻意留的几处：`voided_stock` 和 `revoked` 是两个数（void 不是状态，它是
 *  `revoked` + `note_code=voided`）；`unavailable` 里的两条带着理由而不是 0；
 *  `pr_open` 在 `by_status` 里是 0 —— 死写入的档位也要在，缺档和 0 在屏幕上必须
 *  长得不一样。 */
export function pipelineStats(url: URL): Record<string, unknown> {
  const now = new Date()
  const ago = (minutes: number) => new Date(now.getTime() - minutes * 60000).toISOString()
  return {
    days: windowDays(url),
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
        age_seconds: 2 * 3600,
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
        age_seconds: 26 * 3600,
      },
    ],
    dwell: {
      filed_to_decision: { count: 9, p50_seconds: 4 * 3600, p90_seconds: 30 * 3600, max_seconds: 50 * 3600 },
      filed_to_merge: { count: 6, p50_seconds: 2.1 * 3600, p90_seconds: 19 * 3600, max_seconds: 30 * 3600 },
      open_card_age: { count: 5, p50_seconds: 6 * 3600, max_seconds: 26 * 3600 },
      accepted_not_archived: { count: 2, max_seconds: 12 * 3600 },
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
          at: ago(360),
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
          last_failure_at: ago(120),
          quarantined_until: null,
        },
      ],
    },
    unsettled_dispatches: 0,
  }
}

/** 产品健康。`unavailable` 那两条**今天算不出来** —— 带理由，不画 0。 */
export function productStats(url: URL): Record<string, unknown> {
  const days = readDays(windowDays(url))
  const accepted = [2, 3, 5, 4, 6, 8, 10]
  return {
    days: windowDays(url),
    north_star: {
      total: 38,
      // 上一等长窗口的同一口径合计（环比用）。
      prev_total: 31,
      series: days.map((date, i) => ({ date, accepted: accepted[i] ?? 0 })),
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
}

/** 集成与凭据。**没有 days** —— 存量问题，不是窗口曲线。 */
export function integrationsStats(): Record<string, unknown> {
  return {
    oauth: {
      total: 40,
      expired: 3,
      expiring_7d: 2,
      no_refresh_token: 5,
      note_key: 'integrations.oauthNote',
    },
    passkey: {
      accounts: 120,
      with_passkey: 36,
      coverage: 0.3,
      note_key: 'integrations.passkeyNote',
    },
    delivery: {
      unsent: 2,
      dead_letters: 1,
      oldest_unsent_at: new Date(Date.now() - 3600000).toISOString(),
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
}
