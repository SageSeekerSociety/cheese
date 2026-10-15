/**
 * proto-feedback-fixtures/routes.ts — 预览那个假后端的路由表：路径加方法进来，回什么
 * 由它定。
 *
 * 形状逐字照抄 `backend/app/api/routes/feedback.py` 那几条。写操作真的改内存里的
 * `ROWS`（提交、支持、评论、删除、状态、备注、指派），因为预览是给人看的，但不该是死
 * 的。出口只有 `routes` 和它的返回类型 `MockReply`，由上一层的 barrel 重发出去。
 */
import type { FeedbackCreateBody, FeedbackPriority, FeedbackStatus } from '@/cx_types'

import { adminRoutes } from '../proto-admin-fixtures'
import { withUsageExtras } from '../proto-usage-fixtures'

import { ADMIN_TABS, adminPage, CLOSED, counts, listPage, markRead, sorted, SORTS } from './list'
import { ACCOUNTS, ADDED_ADMINS, adminRoster, isAdminHandle, ROOT_ADMINS } from './members'
import { create, find, ROWS } from './rows'
import { feedbackStats, platformStats, readDays, usageStats, windowDays } from './stats'
import { ago, avatarOf, BASE_MS, comment, detailPage, ME, META, rung, threadPage } from './support'

/** 假数据的一条答复。`data` 是正常路径；`missing` / `refused` / `forbidden` 是三种
 *  「这件事不能发生」，在 `installPreviewFetch` 里分别翻成 404、412、403 —— 预览要看
 *  到真接口的错误面，否则「按钮点下去没反应」这类问题只有在真机上才现形。
 *
 *  412 和 403 是**两件事**，不能合成一个：412 是「这件事现在不能做，别重试」（已经
 *  办完了、今天配额用完了），403 是「你没有这个权限」。合成一个的话，预览里删别人的
 *  评论会得到一句「别重试」，而服务端给的是「只能删除自己的评论」—— 读的人会去查一
 *  个不存在的原因。（目前只有删评论用 403，`ForbiddenError`。）
 *
 *  `invalid`（400）和 `conflict`（409）是同一条纪律的延续，都是名单那两个接口带来的：
 *  「这个 handle 平台上没有」是 400、「他是部署配置里的根管理员」是 409。这两个不能
 *  并进 `refused` 的 412 —— 412 对客户端说的是「这件事现在不能做，别重试」，而这两条
 *  说的是「你请求里那个名字有问题」，改个名字就能成。并进去的话，预览里给根管理员
 *  按删除会得到一句「别重试」，人就会去查一个不存在的重试开关。 */
export type MockReply =
  | { data: unknown }
  | { missing: true }
  | { refused: string }
  | { forbidden: string }
  | { invalid: string }
  | { conflict: string }
  | { failed: string }
  | undefined

// `?fail=1`：布局预览用的开关，判定写在 `routes()` 里、紧挨着用它的地方。
const PREVIEW_FAIL = typeof location !== 'undefined' && new URLSearchParams(location.search).get('fail') === '1'

/** 飞书应用（样例）。App ID 是编的，不对应任何真实应用。 */
const FEISHU_APP = {
  configured: true,
  app_id: 'cli_a7f3c2e9d4b10018',
  domain: 'feishu',
  updated_by: 'andy',
  updated_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
}

/** 提交、支持、评论这些写操作在预览里**真的改内存里的那份数据**：点一下按钮能看见
 *  列表变化，而不是弹一个「预览模式下不可用」。它们是预览，但不该是死的。
 *
 *  导出给 `proto-preview-transport.ts` 调 —— 那一层管出口，这里只管「这条路由回什么」。 */
export function routes(url: URL, method: string, body: unknown): MockReply {
  const path = url.pathname.replace(/^\/api/, '')
  const payload = (body ?? {}) as Record<string, never> & Record<string, unknown>

  if (path === '/feedback/meta' && method === 'GET') return { data: META }
  // 布局预览用：`?fail=1` 让后台各页的读接口一律失败，用来并排看「出错态」长什么样。
  // 权限（meta）和未读数（counts）不跟着失败，不然外壳直接落到「不是管理员」那一档。
  if (PREVIEW_FAIL && method === 'GET' && path !== '/feedback/counts') return { failed: '服务暂时不可用（样例错误）' }
  const admin = adminRoutes(path, method, url, payload)
  if (admin) return admin
  if (path === '/admin/integrations/feishu' && method === 'GET') return { data: FEISHU_APP }
  if (path === '/admin/integrations/feishu' && method === 'PUT') {
    Object.assign(FEISHU_APP, {
      configured: true,
      app_id: payload.app_id,
      domain: payload.domain,
      updated_by: 'andy',
      updated_at: new Date().toISOString(),
    })
    return { data: FEISHU_APP }
  }
  if (path === '/feedback/counts' && method === 'GET') return { data: counts() }
  if (path === '/feedback/read' && method === 'POST') {
    // 游标推到**此刻**，不是「这一条」：`markRead` 的语义是「我全看过了」，所以之后
    // 再问 `counts.unread` 是 0（store 本地也这么做，见那边的注释）。原来这里只回一个
    // 时间戳、不动游标 —— 于是按 `M` 之后徽标被本地归零，重新拉一次计数又跳回 12，
    // 而这件事在界面上看不出是假后端没实现。
    return { data: { last_read_at: markRead() } }
  }
  if (path === '/feedback' && method === 'GET') return { data: listPage(url, url.searchParams.get('tab') ?? 'all') }
  if (path === '/feedback/mine' && method === 'GET') {
    const mine = ROWS.filter((item) => item.author_handle === ME || item.submitted_by_handle === ME)
    return { data: { data: sorted(mine), total: mine.length, counts: counts() } }
  }
  if (path === '/feedback' && method === 'POST') return { data: create(payload as unknown as FeedbackCreateBody) }

  // --- 看板（`/admin/stats/*`）---------------------------------------------
  //
  // 三条路由一个分类（`backend/app/api/routes/admin_stats.py`），切到哪一类才拉哪一类。
  // 这一组的前缀不是 `/admin/feedback`，所以不会被下面那条动态段吃掉。
  //
  // 这里以前还多一条**老路径** `/admin/feedback/stats` 的替身，注释里写着「看板那一页
  // 还没换过来」。那是个教训：它把真环境里的 **400** 盖住了 —— 前端指着一条已经没有的
  // 路由，`stats` 落进 `/admin/feedback/{id}` 被当成一个 uuid 解析，预览一切正常、dev 上
  // 是「看板加载失败」。**替身只该照抄服务端真实存在的东西**；一条为了「两种前端版本都
  // 不报警」而留的别名，代价是发现不了其中一种版本是坏的。
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
  function performanceStats(): Record<string, unknown> {
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
  function pipelineStats(url: URL): Record<string, unknown> {
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
  function productStats(): Record<string, unknown> {
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
  function integrationsStats(): Record<string, unknown> {
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

  const stats = /^\/admin\/stats\/([a-z]+)$/.exec(path)
  if (stats && method === 'GET') {
    if (stats[1] === 'pipeline') return { data: pipelineStats(url) }
    if (stats[1] === 'product') return { data: productStats() }
    if (stats[1] === 'integrations') return { data: integrationsStats() }
    if (stats[1] === 'feedback') return { data: feedbackStats(url) }
    if (stats[1] === 'usage') return { data: withUsageExtras(usageStats(url)) }
    if (stats[1] === 'platform') {
      const plat = platformStats(url) as Record<string, unknown>
      plat.extras = {
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
      }
      return { data: plat }
    }
    if (stats[1] === 'performance') return { data: performanceStats() }
    // 别的没有对应的路由 —— 服务端那是 404，不是「空数据」。
    return { missing: true }
  }

  const adminList = /^\/admin\/feedback$/.exec(path)
  if (adminList && method === 'GET') {
    const tab = url.searchParams.get('tab') ?? 'public'
    const sort = url.searchParams.get('sort') ?? 'new'
    // 不认识的 `tab` / `sort` 报 **400**，不是悄悄退回默认档（`services.list_admin` 那
    // 两处 `BadRequestError`，原话照抄 —— 页面把服务端那句直接显示出来）。管理端猜错
    // 栏位会让人以为「这条反馈不见了」，而它其实在隔壁那一栏；排序更硬：那一页的答案
    // 就是顺序，答成另一种排序等于用同一个标题回答了另一个问题。
    if (!ADMIN_TABS.includes(tab)) return { invalid: `未知的管理视图：${tab}` }
    if (!SORTS.includes(sort)) return { invalid: `未知的排序：${sort}` }
    return { data: adminPage(url, tab, sort) }
  }

  // --- 成员管理（`/admin/members`）----------------------------------------
  //
  // 三条写操作**真的改这份假名单**：加完一个人，右边那两块要当场变（同提交反馈、
  // 支持那条口径 —— 预览是给人看的，但不该是死的）。
  if (path === '/admin/admins' && method === 'GET') return { data: adminRoster() }
  if (path === '/admin/users' && method === 'GET') {
    const q = (url.searchParams.get('q') ?? '').trim().toLowerCase()
    const limit = Number(url.searchParams.get('limit') ?? 20)
    const items = ACCOUNTS.filter(
      (one) => one.handle.toLowerCase().includes(q) || one.nickname.toLowerCase().includes(q)
    )
      .slice(0, limit)
      .map((one) => ({
        ...one,
        avatar_id: avatarOf(one.handle),
        already_admin: isAdminHandle(one.handle),
      }))
    return { data: { items } }
  }
  if (path === '/admin/admins' && method === 'POST') {
    const wanted = String(payload.handle ?? '').trim()
    // 三条拒绝和服务端同一个**类别**，因为页面把服务端那句原话直接显示出来：
    // 预览里给一个别的说法，等于让「按下去看到什么」在预览和真机上不一样。
    if (!wanted) return { invalid: '要加的人不能是空的' }
    if (ROOT_ADMINS.includes(wanted)) {
      return { conflict: `${wanted} 是部署配置里的根管理员，不用在页面上加` }
    }
    if (!ACCOUNTS.some((one) => one.handle === wanted)) {
      return { invalid: `平台里没有 handle 是 ${wanted} 的账号` }
    }
    const created = !isAdminHandle(wanted)
    if (created) {
      ADDED_ADMINS.push({ handle: wanted, added_by_handle: ME, created_at: new Date().toISOString() })
    }
    return { data: { ...adminRoster(), created } }
  }

  const adminRemove = /^\/admin\/admins\/([^/]+)$/.exec(path)
  if (adminRemove && method === 'DELETE') {
    const target = decodeURIComponent(adminRemove[1])
    // 根删不掉：**409，不是静默不动**。页面上那几行本来就没有删除按钮，真按到了
    // 说明两边对不上，那就该说出来（`AdminService.remove_admin` 那条口径）。
    if (ROOT_ADMINS.includes(target)) {
      return { conflict: `${target} 是部署配置里的根管理员，页面上删不掉 —— 改配置要有服务器权限` }
    }
    const at = ADDED_ADMINS.findIndex((one) => one.handle === target)
    const removed = at >= 0
    if (removed) ADDED_ADMINS.splice(at, 1)
    return { data: { ...adminRoster(), removed } }
  }

  const detail = /^\/feedback\/([^/]+)$/.exec(path)
  if (detail && method === 'GET') {
    const item = find(detail[1])
    return { data: item && detailPage(item, url.searchParams.get('after')) }
  }
  const adminDetail = /^\/admin\/feedback\/([^/]+)$/.exec(path)
  if (adminDetail && method === 'GET') {
    const item = find(adminDetail[1])
    return { data: item && detailPage(item, url.searchParams.get('after')) }
  }

  const supports = /^\/feedback\/([^/]+)\/supports$/.exec(path)
  if (supports && (method === 'POST' || method === 'DELETE')) {
    const item = find(supports[1])
    if (!item) return { missing: true }
    if (method === 'POST' && CLOSED.includes(item.status)) {
      // 412，不是 404：反馈看得见，不能发生的是这个动作（`services.support`）。
      // 预览里也照此拒绝 —— 一个前端挡住但预览放行的按钮，正好是「看起来能点、
      // 真机上点了报错」的那类 bug，预览存在的意义就是别让它到那一步。
      return { refused: '这条反馈已经办完了，不再接受支持' }
    }
    item.supports += method === 'POST' ? 1 : -1
    item.supported = method === 'POST'
    return { data: { count: item.supports, supported: item.supported } }
  }

  const comments = /^\/feedback\/([^/]+)\/comments$/.exec(path)
  if (comments && method === 'GET') {
    const item = find(comments[1])
    if (!item) return { missing: true }
    // 楼内那一页：这一份假数据一次把一栋楼的回复给全了，所以 `replies_next_cursor`
    // 恒为 null、这个分支走不到。真走到了（比如以后给某栋楼造更多回复）回一页空的，
    // 而不是把已经给过的那几条再发一遍 —— 那会在屏幕上变成一人两条。
    if (url.searchParams.get('parent_id')) return { data: { items: [], next_cursor: null } }
    const { thread, next } = threadPage(item, url.searchParams.get('after'))
    return { data: { items: thread, next_cursor: next } }
  }
  if (comments && method === 'POST') {
    const item = find(comments[1])
    if (!item) return { missing: true }
    // 折楼和服务端同一处规则：回一条回复时 `parent_id` 落成那栋楼的顶层，而
    // `reply_to_handle` 只在「回的对象本身是回复」时记下来。预览里少折这一层，
    // 画出来就是三层楼 —— 而真实服务端造不出那个形状。
    const target = item.thread.find((c) => c.id === payload.parent_id)
    const created = comment({
      id: `c-${(item.thread.length + 10).toString()}`,
      parent: target ? target.parent_id ?? target.id : null,
      author: ME,
      body: String(payload.body ?? ''),
      minutesAgo: 0,
      replyTo: target?.parent_id ? target.author_handle : undefined,
    })
    item.thread = [...item.thread, created]
    item.comments += 1
    // 新回复要把它那一栋的**总数**也加一。客户端在本地做同一件事（`addComment`），
    // 但那是为了让眼前这一屏对得上；这里改的是「数据本身」，下次重新拉详情时
    // 才不会回退成少一条。
    const top = item.thread.find((c) => c.id === created.parent_id)
    if (top) top.reply_count += 1
    return { data: created }
  }

  const commentLike = /^\/feedback\/([^/]+)\/comments\/([^/]+)\/likes$/.exec(path)
  if (commentLike) {
    const item = find(commentLike[1])
    const target = item?.thread.find((c) => c.id === commentLike[2])
    if (!item || !target) return { missing: true }
    const wanted = method === 'POST'
    // 和 `toggleSupport` 那份一样：**回的是写完之后的服务端计数**，不是本地 ±1。
    // 同一个人重复 POST/DELETE 不叠加，这也正是唯一约束在做的事。
    if (target.liked !== wanted) {
      target.liked = wanted
      target.likes += wanted ? 1 : -1
    }
    return { data: { count: target.likes, liked: target.liked } }
  }

  const oneReport = /^\/feedback\/([^/]+)$/.exec(path)
  if (oneReport && method === 'DELETE') {
    const item = find(oneReport[1])
    if (!item) return { missing: true }
    // 服务端两条路分得很清楚：看不见 → 404，看得见但删不掉 → 403。预览里假后端的
    // 世界只有一个人（没有登录态），所以能走到这里的都判为「能删」；`can_delete`
    // 那半由页面自己按数据画按钮，和真机同一套。
    if (!item.can_delete) return { forbidden: '只能删除自己提交的反馈' }
    // 软删的**可观察那一半**：它从每一份列表里消失（真机上由服务端的读侧过滤完成）。
    ROWS.splice(ROWS.indexOf(item), 1)
    return { data: { deleted: true } }
  }

  const oneComment = /^\/feedback\/([^/]+)\/comments\/([^/]+)$/.exec(path)
  if (oneComment && method === 'DELETE') {
    const item = find(oneComment[1])
    if (!item) return { missing: true }
    const id = oneComment[2]
    if (!item.thread.some((c) => c.id === id && c.can_delete)) {
      // 「看得见但删不掉」（`may_delete_comment`），所以是 403 而不是 404，原话照抄
      // 服务端的 `ForbiddenError`。预览里也照此拒绝 —— 一个前端挡住而预览放行的
      // 按钮，正是「看着能点、真机上点了报错」那类 bug。
      return { forbidden: '只能删除自己的评论' }
    }
    // 级联：删一条顶层评论，它那一栋的回复跟着一起走（服务端同一个形状）。
    const goneRows = item.thread.filter((c) => c.id === id || c.parent_id === id)
    const gone = new Set(goneRows.map((c) => c.id))
    const kept = item.thread.filter((c) => !gone.has(c.id))
    item.comments -= gone.size
    item.thread = kept
    // 删掉的那几条回复，如果它那一栋还在（删的是一条楼内回复），那一栋的总数要跟着
    // 减：那个数是「展开更多 N 条回复」上写的字，少了这一步，删完之后按钮上写的
    // 数字和展开出来能看到的条数就对不上了。（`goneRows` 是删之前那一份 —— 上面
    // 已经把 `item.thread` 换成 `kept` 了，回头再按 id 找是找不到的。）
    for (const removed of goneRows) {
      if (!removed.parent_id) continue
      const top = kept.find((c) => c.id === removed.parent_id)
      if (top) top.reply_count = Math.max(0, top.reply_count - 1)
    }
    return { data: { ok: true } }
  }

  const status = /^\/admin\/feedback\/([^/]+)\/status$/.exec(path)
  if (status && method === 'POST') {
    const item = find(status[1])
    if (!item) return { missing: true }
    const wanted = payload.status as FeedbackStatus
    // 设成**同一个**状态是幂等的（`services.set_status`），不写第二条时间线：相隔一秒的
    // 两条一模一样的记录，读起来像历史出了 bug。按 `3`（已上线）再按一次是最常见的
    // 那种重复，而预览里那第二行是看得见的。
    if (item.status !== wanted) {
      item.status = wanted
      item.last_activity_at = new Date(BASE_MS).toISOString()
      item.timeline = [...item.timeline, rung(wanted, ME, item.last_activity_at)]
    }
    return { data: item }
  }

  const notes = /^\/admin\/feedback\/([^/]+)\/notes$/.exec(path)
  if (notes && method === 'POST') {
    const item = find(notes[1])
    if (!item) return { missing: true }
    item.notes = [
      ...item.notes,
      {
        id: `n-${item.notes.length + 10}`,
        author_handle: ME,
        author_avatar_id: avatarOf(ME),
        body: String(payload.body ?? ''),
        created_at: ago(0),
      },
    ]
    return { data: item }
  }

  const patch = /^\/admin\/feedback\/([^/]+)$/.exec(path)
  if (patch && method === 'PATCH') {
    const item = find(patch[1])
    if (!item) return { missing: true }
    if (payload.priority) item.priority = payload.priority as FeedbackPriority
    if (payload.assignee_handle !== undefined) {
      // 空串是**清掉**指派人（`services.patch_admin` 那句 `or None`）—— 提交框里把人
      // 删掉发的就是一个空串，`?? null` 那种写法会把它存成一个空 handle，界面上看着像
      // 「指派给了一个没有名字的人」。`undefined` 才是「这次请求里没提到它」。
      item.assignee_handle = (payload.assignee_handle as string | null) || null
    }
    if (payload.security !== undefined && Boolean(payload.security) !== item.security) {
      item.security = Boolean(payload.security)
      // 标成安全问题是一次**路由决定**：提的人应当看得见，而且从这一刻起同事看不见它
      // 了。两件事从行本身都看不出来，所以进时间线 —— 和 `patch_admin` 一样，记的是
      // **当时**的状态（改安全标记不改状态）。
      item.timeline = [...item.timeline, rung(item.status, ME, new Date(BASE_MS).toISOString())]
    }
    return { data: item }
  }

  return undefined
}
