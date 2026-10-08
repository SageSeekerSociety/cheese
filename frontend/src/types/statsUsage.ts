/** 用量看板那一份响应（`GET /admin/stats/usage`）的形状：谁在用多少，以及订阅通路
 *  身后那个 Claude 账号池。`api.ts` 再导出，各屏照旧从 `@/api`（或 `lib/adminStats.ts`）
 *  取。
 *
 *  为什么单开一个文件：`api.ts` 已经越过 1000 行的尺寸上限，按
 *  `.claude/scripts/check-file-sizes.py` 只许变小 —— 往里加字段的唯一办法就是把它
 *  要加的那个类型搬出来。这里和 `types/compute.ts`、`types/roomOutput.ts` 是同一类
 *  东西。账号池的判据在 `backend/app/domain/platform_stats/claude_pool.py`。 */

/** 用量那一块。`unpriced_tokens` 与 `cost_usd` **一起读才对**：前者是「这些 token
 *  算不出价钱」（模型没有单价，行上的 0 是「没有价」不是「免费」），少了它，几百万
 *  token 上印一个 `$0.0000` 读起来像「这个月没花钱」。 */
export interface StatsUsage {
  days: number
  totals: { tokens: number; calls: number; cost_usd: number; unpriced_tokens: number }
  series: { date: string; tokens: number; calls: number; cost_usd: number }[]
  /** 柱状图的每一根都带 id 和名字。**今天柱子不点得开**（看板上那一张只报数），
   *  `project_id` 是给以后的钻取和「同名项目」留的**身份** —— 名字在平台上不唯一，
   *  只按名字连线，两个同名项目会合成一根柱子。 */
  top_projects: { project_id: string; name: string; tokens: number; cost_usd: number }[]
  /** 按模型拆。和 `by_route` 是两个正交的切口：「贵的是模型还是计费方式」要两个一起看。 */
  by_model: {
    model: string
    tokens: number
    calls: number
    cost_usd: number
    /** 同一行上的「算不出价钱」的那部分。0 是「没有价」不是「免费」。 */
    unpriced_tokens: number
  }[]
  /** 按供给通路拆：gateway（网关）/ subscription（订阅）/ native（自带凭据）/ ''（旧数据）。 */
  by_route: {
    route: string
    tokens: number
    calls: number
    cost_usd: number
    unpriced_tokens: number
  }[]
  /** 额度燃尽。已耗尽 / 快烧完 / 不限量是**三个互斥集合** —— unlimited 是没有 grant。 */
  credits: {
    exhausted: {
      project_id: string | null
      name: string
      credits_total: number
      credits_used: number
      credits_remaining: number
      ratio: number
    }[]
    low: {
      project_id: string | null
      name: string
      credits_total: number
      credits_used: number
      credits_remaining: number
      ratio: number
    }[]
    unlimited_project_ids: string[]
    unlimited_count: number
    burn: {
      credits_in_window: number
      credits_per_day: number
      method: string
    }
  }
  /** 订阅通路身后那个 Claude 账号池。池子只活在跑计量代理的那台机器上，这里是**代理
   *  写在账本旁边的一份快照** —— 这一块里唯一不来自数据库的东西，也因此「读不到」是
   *  常态（开发环境根本不跑订阅版代理），不是故障。行与 `reason` 的判据在
   *  `backend/app/domain/platform_stats/claude_pool.py`。 */
  claude_accounts: StatsClaudePool
  /** 上一等长窗口的同口径合计（环比用），形状与 token / 调用 / 成本三张卡一一对应。
   *  可选：旧后端还没有它，前端按「键在才画 delta」接线。 */
  prev?: { tokens: number; calls: number; cost_usd: number }
}

/** 池子里的一张账号。`until` 是解冻的**绝对**时刻（Unix 秒），不是「还有多少秒」——
 *  倒计时写下来的那一刻就开始变旧，而这一份文件可能已经放了很久。 */
export interface StatsClaudeAccount {
  name: string
  /** 后端只写这四个值。代理是独立发布链，它先写下第五个时后端会收成 `unknown`。 */
  state: 'available' | 'cooling' | 'disabled' | 'unknown'
  /** 解冻时刻；`available` / `disabled` 都没有可等的时刻。 */
  until: number | null
  /** 连续失败次数。读不出来是 `null`（不是 0）——「0 次」和「不知道」不能画成一个样。 */
  failures: number | null
}

/** 账号池快照。`accounts` 为空时 `reason` 是一句「为什么看不见」的**代号**，界面翻成
 *  人话（`UNAVAILABLE_KEY` 那套）；有行时是 `null`。 */
export interface StatsClaudePool {
  accounts: StatsClaudeAccount[]
  reason: string | null
  /** 代理写下这一份的时刻（Unix 秒）。 */
  written_at: number | null
  /** 它有多旧。**由服务端算**：客户端的钟和那一台的钟未必一致。 */
  age_seconds: number | null
  /** 旧到不能当「现在」用（后端判的，一小时）。行照常给，界面要注明这是什么时间的。 */
  stale: boolean
  /** 最早解冻所需的秒数，由行上的绝对时刻现算（不取文件里那个同龄的计数）。看板读的
   *  是行上的时刻 —— 倒计时写下的那一刻就开始变旧；这个字段是同一个时刻的另一种写法，
   *  给「还要等多久」这种问法用。 */
  retry_after: number | null
}
