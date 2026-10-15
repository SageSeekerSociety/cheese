/**
 * proto-feedback-fixtures/stats.ts — 看板的形状：反馈、用量、平台三块，逐字照抄后端那
 * 几个服务的输出（`/admin/stats/*`）。
 *
 * 窗口按 **UTC 天**切、最早的一天在前、缺的那天补 0 —— 这几条是形状的一部分，不是实现
 * 细节。反馈那块从 `ROWS` 数出来；用量和平台在 `ROWS` 里没有对应物，是编的，编得自洽的
 * 四条理由写在各自那一段上面。
 */
import type { FeedbackDetail, FeedbackStatus } from '@/cx_types'

import { counts } from './list'
import { ADDED_ADMINS, ROOT_ADMINS } from './members'
import { ROWS } from './rows'
import { BASE_MS } from './support'

/* ---- 看板（`/admin/stats/*`）的三块 -------------------------------
 *
 * 服务端**一个分类一条路由**（`backend/app/api/routes/admin_stats.py`），形状钉死在
 * `domain/platform_stats/services.py` 里，这里逐字照抄：`days` 原样回显、`series`
 * 恒有 `days` 行、**最早的一天在前**、缺的那天是 0。
 *
 * `series` 和 `counts` 都从 `ROWS` 里数出来，不另编一套：看板上的「7 日新增」和队列
 * 里那 7 天真有的条目必须是同一批 —— 各数一遍的症状是「看板和反馈管理页对同一天给出
 * 两个数」，而两边各自看着都对。
 *
 * 三块里有两块（用量、平台）在 `ROWS` 里没有对应数据，那两块是**编的**；编也得编得
 * 自洽，怎么来的写在各自那一段上面。
 */

/** 窗口天数。服务端签名是 `ge=1 le=90`、默认 7 —— 三个数各有一处理由（那个模块的
 *  docstring）：默认 7 是页头上那句「过去 7 天」，上界 90 是这个读本身的成本，下界 1 是
 *  「0 天画不出一条折线」。缺省、不是个数、小于 1 时退到 7，超过上界时截到 90 ——
 *  这两条**不是服务端的行为**（那边是 422 / 400），是预览里「不让人因为一个参数打不开
 *  页面」的取舍；页面自己从不这么问。 */
export function windowDays(url: URL): number {
  const asked = Number(url.searchParams.get('days'))
  if (!Number.isFinite(asked) || asked < 1) return 7
  return Math.min(90, Math.floor(asked))
}

/** 一个 UTC 日的开始。窗口按 **UTC 的天**切，和 `windows.utc_day_window` 同一把尺子
 *  —— 按本地时区切的话，`days=7` 会跨进第八天的一小段，而 series 里没有它的位置：
 *  那一段被算进总量却不出现在图上，两个数对不上而两边各自都「对」。 */
function utcDayStart(ms: number): number {
  const at = new Date(ms)
  return Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate())
}

/** `YYYY-MM-DD` —— `dense_series` 里 `date.isoformat()` 的那个形状。 */
function dayKey(ms: number): string {
  return new Date(utcDayStart(ms)).toISOString().slice(0, 10)
}

/** 窗口里的日子：**最早在前、长度恒等于 `days`**。补 0 的判据只能是这一份 ——
 *  按「有数据的那几天」补的话，7 天的线会画成 5 天，而且看不出来（断点处是一条平滑的
 *  线，不是一段空白）。 */
export function readDays(days: number): string[] {
  const since = utcDayStart(BASE_MS) - (days - 1) * 86_400_000
  return Array.from({ length: days }, (_, i) => dayKey(since + i * 86_400_000))
}

/** 上一个等长窗口的日子（`[since-days, since)`）—— 各分类 `prev` 合计的那一份，
 *  和后端 `services.py` 的 prev 窗口同一把尺子。 */
function prevDays(days: number): string[] {
  return readDays(days * 2).slice(0, days)
}

/** 把一个 `日期 → 数` 的稀疏表铺成 `days` 行，缺的那天写 0。列名由调用方点名（和
 *  `dense_series` 一样，不猜）：猜错的表现是一栏全是 0，而没有任何一处会报错。
 *
 *  行类型里 `date` 是**例外**，所以它单独写出来而不是并进索引签名：写成
 *  `{date: string} & Record<string, number>` 的话 `date` 会同时是 string 和 number
 *  （交集等于 never），字面量 `{date}` 当场就不满足它 —— 类型上说得通、代码里建不出来
 *  的那种形状。 */
function dense(
  days: number,
  columns: Record<string, Map<string, number>>
): Array<{ date: string; [column: string]: number | string }> {
  return readDays(days).map((date) => {
    const row: { date: string; [column: string]: number | string } = { date }
    for (const name of Object.keys(columns)) row[name] = columns[name].get(date) ?? 0
    return row
  })
}

/** `日期 → 那一天有几条反馈`（按 id 去重）。 */
function markDay(table: Map<string, Set<string>>, day: string, id: string): void {
  const ids = table.get(day) ?? new Set<string>()
  ids.add(id)
  table.set(day, ids)
}

function dayCounts(table: Map<string, Set<string>>): Map<string, number> {
  const out = new Map<string, number>()
  for (const [day, ids] of table) out.set(day, ids.size)
  return out
}

/** 窗口内按天新建的反馈数。窗口外那几天落在 `readDays` 之外的键上，`dense` 不读它们
 *  —— 和 SQL 那句 `created_at >= since AND < until` 是同一件事。 */
function createdDays(): Map<string, number> {
  const table = new Map<string, Set<string>>()
  for (const item of ROWS) markDay(table, dayKey(Date.parse(item.created_at)), item.id)
  return dayCounts(table)
}

/** 「那一天有几条反馈**到过**这个状态」。
 *
 *  问的是**时间线**，不是「现在是不是这个状态」（`_reached_since` / `reached_series`）：
 *  一条后来又被退回的条目照样算「那天解决过」，而那正是分诊要看见的一批。
 *
 *  同一个状态在时间线上可以有第二行（改了又改回来），服务端数的是
 *  `count(distinct feedback_id)` —— 「几条反馈」而不是「到过几次」，所以这里按 id 去重：
 *  假后端把「到过 2 次」画成 2，图上就比真环境高一格，而那种偏差没人看得出来。 */
function reachedDays(status: FeedbackStatus): Map<string, number> {
  const table = new Map<string, Set<string>>()
  for (const item of ROWS) {
    for (const step of item.timeline) {
      if (step.status === status) markDay(table, dayKey(Date.parse(step.at)), item.id)
    }
  }
  return dayCounts(table)
}

/** 反馈那一块：**全量口径**的总量 / 四栏 / 四级状态 + 按天的新增 / 解决 / 上线。
 *
 *  形状和 `PlatformStatsService.feedback` 那一个推导式一一对应（`total` / `columns` /
 *  `status` / `unread` / `series`）。**口径是全量**：看板数的是整个板子，不是公开那一
 *  臂 —— 此前它走 `counts()`（被 `PUBLIC_ONLY` 收窄），而 `series` 是全量，卡片和曲线
 *  各答各的问题。这里直接从 `ROWS` 数，和曲线同一批行，两边永远一致。
 *
 *  `columns` 的四个是**筛选不是划分**（`agent` 是来源，和公开/私密重叠），所以它们加
 *  起来不等于 `total.all` —— 页面上那句口径说的就是这件事。 */
export function feedbackStats(url: URL): Record<string, unknown> {
  const days = windowDays(url)
  const live = ROWS
  const isClosed = (i: FeedbackDetail) => i.status === 'resolved' || i.status === 'deployed'
  const total = {
    all: live.length,
    open: live.filter((i) => !isClosed(i)).length,
    closed: live.filter(isClosed).length,
    unassigned: live.filter((i) => !i.assignee_handle && !isClosed(i)).length,
    urgent_open: live.filter((i) => !isClosed(i) && (i.priority === 'high' || i.priority === 'urgent')).length,
  }
  const columns = {
    public: live.filter((i) => i.visibility === 'public' && !i.security).length,
    private: live.filter((i) => i.visibility === 'private' && !i.security).length,
    agent: live.filter((i) => i.author_is_agent).length,
    security: live.filter((i) => i.security).length,
  }
  const status = {
    received: live.filter((i) => i.status === 'received').length,
    in_progress: live.filter((i) => i.status === 'in_progress').length,
    resolved: live.filter((i) => i.status === 'resolved').length,
    deployed: live.filter((i) => i.status === 'deployed').length,
  }
  const created = createdDays()
  const resolved = reachedDays('resolved')
  return {
    days,
    total,
    columns,
    status,
    unread: counts().unread,
    series: dense(days, {
      created,
      resolved,
      deployed: reachedDays('deployed'),
    }),
    // 上一等长窗口的合计（`[since-days, since)`）—— KPI 卡的环比差从这里出。
    prev: {
      created: prevDays(days).reduce((acc, day) => acc + (created.get(day) ?? 0), 0),
      resolved: prevDays(days).reduce((acc, day) => acc + (resolved.get(day) ?? 0), 0),
    },
  }
}

/* ---- 用量 ----------------------------------------------------------------
 *
 * 这一块 `ROWS` 里没有对应数据（那是一份反馈，一行用量也没有），所以是**编的**。
 * 编得自洽的四条：
 *
 *  * `totals` 就是 `series` 那几列各自的和，不是另取一个数 —— 图上的柱子和卡片上的
 *    合计对不上，是这一块最容易被信以为真的假。
 *  * `cost_usd` 由 token 按一个单价算出来（`USD_PER_MTOK`），不是随手编一个钱数：
 *    「几百万 token 配 $0.00」正是这一块最容易出的那种错。
 *  * `unpriced_tokens` 是其中**算不出价钱**的那一份（没有单价的行 `cost_usd = 0`
 *    意思是「没有价」，不是免费），它从 `cost_usd` 的分母里扣掉。
 *  * **周日恒为 0**：每个 7 天窗口里正好有一个周日，所以「缺的天补 0」这件事在
 *    **每一条**窗口里都看得见，而不是碰巧有个空档。
 *
 * 同一天的数字只由**日期**决定（`hashDay`）：假数据也得能对着一张截图复现，刷新一次
 * 变一个数是最坏的一种假。 */

/** 一百万个 token 三美元 —— 真环境订阅与按量混着，这个数落在「几美元一千万」那个
 *  量级上就够看图了。 */
const USD_PER_MTOK = 3

/** 一天一个数：FNV-1a 折出来的。判据里**没有** `Date.now()`。 */
function hashDay(seed: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash
}

/** 0 = 周日。和窗口同一把尺子（UTC 的天）。 */
function weekdayOf(day: string): number {
  return new Date(`${day}T00:00:00Z`).getUTCDay()
}

function usageOfDay(day: string): {
  tokens: number
  calls: number
  cost_usd: number
  unpriced_tokens: number
} {
  // 一行一次调用（`/v1/messages`），一天六到十七次。周日 0。
  const calls = weekdayOf(day) === 0 ? 0 : 6 + (hashDay(day) % 12)
  // 一次调用两三千 token，和真环境一个量级。
  const tokens = Math.round((calls * (2400 + (hashDay(`t${day}`) % 2000))) / 100) * 100
  // 订阅计费那一份占 2%~6%。这个数只为让「有几百万 token 算不出价钱」在界面上说得
  // 出来（`unpriced_tokens()`），所以四舍五入到百位就够了。
  const unpriced_tokens = Math.round((tokens * (2 + (hashDay(`u${day}`) % 5))) / 100 / 100) * 100
  const usd = ((tokens - unpriced_tokens) / 1_000_000) * USD_PER_MTOK
  return { tokens, calls, cost_usd: Number(usd.toFixed(4)), unpriced_tokens }
}

/** 窗口内那几天的用量，加它们各自的和。`usageStats` 和老路径那条别名都从这里读，
 *  所以「合计」在两个形状里是同一个数。 */
function usageWindow(days: number) {
  const rows = readDays(days).map((date) => ({ date, ...usageOfDay(date) }))
  const sum = (key: 'tokens' | 'calls' | 'cost_usd' | 'unpriced_tokens'): number =>
    rows.reduce((acc, row) => acc + row[key], 0)
  return {
    rows,
    tokens: sum('tokens'),
    calls: sum('calls'),
    cost: Number(sum('cost_usd').toFixed(4)),
    unpriced: sum('unpriced_tokens'),
  }
}

/** 柱状图上那几根柱子。名字是**仓库里真有的**（每条后面的出处就是它出现的地方），
 *  不是六个看起来像项目的字符串。`project_id` 是固定的假 uuid —— 预览里没有项目这一
 *  层，柱子点不开是预览的边界，不是这一份数据的错。 */
const TOP_PROJECTS: { id: string; name: string }[] = [
  // 「知是平台后端」：`backend/tests/integration/test_artifact_is_the_repository.py`
  // 里建的项目名。
  { id: '4a1c0f6e-7b52-4d9a-9c31-2f8d5a0b7e11', name: '知是平台后端' },
  // 「cheese 自建」：`backend/scripts/device_selfhost_smoke.py` 与
  // `machine_chain_check.py` 里那个默认的项目名（`device_capability_setup.py` 也有）。
  { id: 'b7d2e904-3c18-4a67-8f25-6e0c1d9a4b52', name: 'cheese 自建' },
  // 「看板项目」：`backend/tests/integration/test_admin_stats.py` 里那一条。
  { id: '0c93f2a1-5d47-4e88-b1a6-7c4b2e0f9d38', name: '看板项目' },
  // 「创新项目入驻 2026 秋」：`backend/scripts/sim_real.py` / `seed_demo.py`
  // 里那条演示项目。
  { id: 'e58b1a37-9f24-4c10-8d73-1a6e0b5c7f92', name: '创新项目入驻 2026 秋' },
  // 「AI 课程推荐系统」：同上。
  { id: '3f7c8d20-6a51-4b93-9e08-5d2c7a1f04b6', name: 'AI 课程推荐系统' },
  // 「cheese_status」：`backend/tests/unit/test_cheese_cli.py` 里那个项目名。
  { id: 'd21e6b85-4c03-4f7a-a5b9-8e1d3c60f7a4', name: 'cheese_status' },
  // 「结题报告」：后端用例里的项目名。
  { id: '7b0a4e6c-2d95-4e3b-8c17-a9f5d2b803e1', name: '结题报告' },
]

/** 前六名各占多少，**最后一名吃掉余数** —— 这样加起来正好等于合计（差一百 token 就是
 *  「柱子和合计对不上」，而那是这一块唯一会被核对的地方）。份额本身是编的，但它是这份
 *  假数据里唯一一处编的地方，而且可核对。 */
const TOP_PROJECT_WEIGHTS = [0.24, 0.18, 0.13, 0.11, 0.09, 0.07]

function splitShares(total: number, weights: number[]): number[] {
  const out: number[] = []
  let rest = total
  for (const weight of weights) {
    const part = Math.round((total * weight) / 100) * 100
    out.push(part)
    rest -= part
  }
  out.push(rest)
  return out
}

/** 窗口内用量最高的几个项目（`UsageRepository.top_projects` 那一支）。 */
function topProjects(tokens: number, unpriced: number): Record<string, unknown>[] {
  const tokensBy = splitShares(tokens, TOP_PROJECT_WEIGHTS)
  const unpricedBy = splitShares(unpriced, TOP_PROJECT_WEIGHTS)
  return TOP_PROJECTS.map((project, i) => ({
    project_id: project.id,
    name: project.name,
    tokens: tokensBy[i],
    // 算得出价钱的那一份才进 `cost_usd`：和 `unpriced_tokens` 是同一条口径，柱子上的
    // 钱和卡片上的钱要能对得上。
    cost_usd: Number((((tokensBy[i] - unpricedBy[i]) / 1_000_000) * USD_PER_MTOK).toFixed(4)),
  }))
}

/** 用量那一块：窗口内的总量、按天序列、最花钱的几个项目。 */
export function usageStats(url: URL): Record<string, unknown> {
  const days = windowDays(url)
  const bucket = usageWindow(days)
  // 上一等长窗口（`[since-days, since)`）的同一口径合计 —— KPI 环比的数据源。
  // 同一天的数字只由日期决定（`hashDay`），所以 prev 和当前窗口一样可复现。
  const prevRows = prevDays(days).map((date) => usageOfDay(date))
  const prevSum = (key: 'tokens' | 'calls' | 'cost_usd'): number => prevRows.reduce((acc, row) => acc + row[key], 0)
  return {
    days,
    // 上面那三列的和，就是这一份 totals —— 这一块没有第二个读法。
    totals: {
      tokens: bucket.tokens,
      calls: bucket.calls,
      cost_usd: bucket.cost,
      unpriced_tokens: bucket.unpriced,
    },
    series: bucket.rows.map(({ date, tokens, calls, cost_usd }) => ({ date, tokens, calls, cost_usd })),
    top_projects: topProjects(bucket.tokens, bucket.unpriced),
    // 两个正交的拆分。**加起来都等于 totals.tokens** —— 「柱子和合计对不上」是这一块
    // 唯一会被核对的地方。模型那几条是仓库里真跑过的（`deploy/gateway/config.yaml`、
    // 项目记忆里的智谱/小米端点），通路那三条是 `ResourceUsage.route` 的全部取值。
    by_model: byModel(bucket),
    by_route: byRoute(bucket),
    prev: {
      tokens: prevSum('tokens'),
      calls: prevSum('calls'),
      cost_usd: Number(prevSum('cost_usd').toFixed(4)),
    },
  }
}

const MODELS = ['glm-4.6', 'glm-5.3-flash', 'mimo-v2.6-pro', 'claude-sonnet-5']
const ROUTES: { key: string; weight: number }[] = [
  { key: 'gateway', weight: 0.62 },
  { key: 'subscription', weight: 0.28 },
  { key: 'native', weight: 0.1 },
]

function byModel(bucket: ReturnType<typeof usageWindow>) {
  const shares = splitShares(bucket.tokens, [0.46, 0.24, 0.18])
  // 最后一条吃掉余数，和 `topProjects` 同一条规则。
  shares.push(bucket.tokens - shares.reduce((acc, n) => acc + n, 0))
  return MODELS.map((model, i) => {
    const tokens = shares[i]
    // 订阅那一份全在前两个模型上（和 `by_route` 对得上：未定价的总量相等）。
    const unpriced = i < 2 ? Math.round((bucket.unpriced * (i === 0 ? 0.7 : 0.3)) / 100) * 100 : 0
    const usd = ((tokens - unpriced) / 1_000_000) * USD_PER_MTOK
    return {
      model,
      tokens,
      calls: Math.max(1, Math.round(tokens / 3000)),
      cost_usd: Number(usd.toFixed(4)),
      unpriced_tokens: unpriced,
    }
  })
}

function byRoute(bucket: ReturnType<typeof usageWindow>) {
  const shares = splitShares(bucket.tokens, [ROUTES[0].weight, ROUTES[1].weight])
  shares.push(bucket.tokens - shares.reduce((acc, n) => acc + n, 0))
  return ROUTES.map((route, i) => {
    const tokens = shares[i]
    // **样例里的未定价全在订阅那一行**（按单价估算之前记下的订阅用量）。这一行
    // 和 KPI 里「未定价 token」那个数必须相等，否则「来源就在这里」那句话是假的。
    const unpriced = route.key === 'subscription' ? bucket.unpriced : 0
    const usd = ((tokens - unpriced) / 1_000_000) * USD_PER_MTOK
    return {
      route: route.key,
      tokens,
      calls: Math.max(1, Math.round(tokens / 3000)),
      cost_usd: Number(usd.toFixed(4)),
      unpriced_tokens: unpriced,
    }
  })
}

/* ---- 平台 ----------------------------------------------------------------
 *
 * `people.total` 是 `members.ts` 里成员管理那一段注释里那个数（1199 个账号，`admin_members.py`
 * 的注释记着同一个数），`admins` 是**当场数出来的**名单长度（根 ∪ 页面上加的）——
 * 看板另记一个数的话，症状是「看板说 9 个、成员管理页列出来 8 个」。
 *
 * `new` 是窗口内新增的账号数，也就等于 `series` 里那些 `created` 的和：一个给总量、
 * 一个给形状，两个数来自同一份读数。周日恒为 0（和用量同一条理由）。
 *
 * `machines` 四个数是四张台账的**存量**，不是在线数（在线状态住在进程内存里，库里
 * 没有那一列，理由在 `platform_stats/repositories.py` 的模块 docstring 里）。预览里
 * 没有设备页，这四个数**没有对照物**，所以它们只是四个量级上说得过去的常数，别当读数用。
 */
const ACCOUNT_TOTAL = 1199
const MACHINE_STOCK = {
  devices: 7,
  hosted_devices: 5,
  warm_machines: 3,
  cloud_hosts: 2,
}

/** 存量里 agent 账号的个数（真人 = `ACCOUNT_TOTAL` 减去它）。判据是 `agent_bindings`
 *  —— 预览没有那张表，这里记一个常量，量级落在「两位数」上够看图了。 */
const AGENT_ACCOUNTS = 12

function signupsOfDay(day: string): number {
  return weekdayOf(day) === 0 ? 0 : hashDay(`s${day}`) % 4
}

/** 平台那一块：账号的存量与新增、设备台账的存量。 */
export function platformStats(url: URL): Record<string, unknown> {
  const days = windowDays(url)
  const created = new Map<string, number>()
  const humanCreated = new Map<string, number>()
  const agentCreated = new Map<string, number>()
  for (const day of readDays(days)) {
    const total = signupsOfDay(day)
    // 真人 / Agent 的拆分：一天 0–2 个 agent 账号，只由日期决定（和 `usageOfDay` 同
    // 一条纪律 —— 假数据也得能对着一张截图复现）。
    const agents = Math.min(total, hashDay(`a${day}`) % 3)
    created.set(day, total)
    humanCreated.set(day, total - agents)
    agentCreated.set(day, agents)
  }
  const sum = (table: Map<string, number>): number => [...table.values()].reduce((acc, n) => acc + n, 0)
  const prevNew = prevDays(days).reduce((acc, day) => acc + signupsOfDay(day), 0)
  return {
    days,
    people: {
      total: ACCOUNT_TOTAL,
      new: sum(created),
      prev_new: prevNew,
      // 名单是**活的**：成员管理页加了人，这里下一次就是新数。
      admins: ROOT_ADMINS.length + ADDED_ADMINS.filter((row) => !ROOT_ADMINS.includes(row.handle)).length,
      humans: ACCOUNT_TOTAL - AGENT_ACCOUNTS,
      agents: AGENT_ACCOUNTS,
      new_humans: sum(humanCreated),
      new_agents: sum(agentCreated),
      series: dense(days, { created, human_created: humanCreated, agent_created: agentCreated }),
    },
    machines: { ...MACHINE_STOCK },
    // **这一刻**的健康度（不是上面两组的存量/窗口），判据与 `/health/detailed` 同源。
    health: {
      overall: 'healthy',
      checks: {
        database: { status: 'up' },
        redis: { status: 'up' },
        event_loop: { status: 'up', detail: 3.4 },
        routes: { status: 'up' },
      },
    },
  }
}
