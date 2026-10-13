/**
 * 用量看板上 `usageStats()` 之外**贴上去**的假数据：额度燃尽、Claude 账号池（真后端里
 * 这两块也各有各的来源：额度来自 grants，池子来自那台代理机器上的快照），以及每小时的
 * 序列和最近的几行账本（`withUsageExtras` 一并贴上）。
 *
 * 为什么单开一个文件：`proto-feedback-fixtures.ts` 已经越过 1000 行的尺寸上限
 * （`.claude/scripts/check-file-sizes.py`），按要求只许变小 —— 往那边加东西的唯一办法
 * 就是先搬出去一块。这一份只被 `proto-feedback-fixtures.ts` 引用，进预览构建、不进真实
 * 构建，理由同它（见那个文件顶部的说明）。
 */

/** Claude 账号池。三张账号各一种状态（可用 / 冷却 / 已停用），解冻时刻从这一刻起算
 *  —— 这是预览里唯一一个「现在」还说得通的数（同 `usageOfDay` 那条纪律，假数据也得对
 *  得上一张截图）。 */
export function claudePool(): Record<string, unknown> {
  const now = Date.now() / 1000
  return {
    accounts: [
      { name: 'primary', state: 'available', until: null, failures: 0 },
      { name: 'claude-2', state: 'cooling', until: now + 900, failures: 2 },
      { name: 'claude-3', state: 'disabled', until: null, failures: 6 },
    ],
    reason: null,
    written_at: now,
    age_seconds: 0,
    stale: false,
    retry_after: 900,
  }
}

/** 额度燃尽。三个互斥名单 + 从 resource_usage 推的燃烧速率。 */
export function creditsBurnout(): Record<string, unknown> {
  return {
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
  }
}

/** 每小时的 token（北京时间，带 +08:00），7 天以内的窗口才有，和真后端一样补到「现在」
 *  这个小时为止 —— 预览里把「现在」定在最后一天的 17:00。最近的账本行：订阅通路一行
 *  一次请求，网关一行是一个模型的一段合计（所以那一行的输入可以上百万）。 */
function hourlyAndRecent(days: number, dates: string[]): Record<string, unknown> {
  const hours: { hour: string; tokens: number; calls: number; cost_usd: number }[] = []
  dates.forEach((date, d) => {
    const last = d === dates.length - 1 ? 17 : 23
    for (let h = 0; h <= last; h++) {
      const tokens = (((d * 24 + h) * 7919) % 97) * 1200 + (h >= 9 && h <= 22 ? 40_000 : 2_000)
      hours.push({ hour: `${date}T${String(h).padStart(2, '0')}:00:00+08:00`, tokens, calls: 3, cost_usd: 0.01 })
    }
  })
  const today = dates[dates.length - 1] ?? '2026-10-12'
  const row = (at: string, model: string, route: string, input: number, output: number, cache: number | null) => ({
    at: `${today}T${at}+08:00`,
    project_id: null,
    project: '知是平台后端',
    handle: route === 'gateway' ? null : 'lin',
    model,
    route,
    kind: 'chat',
    input_tokens: input,
    output_tokens: output,
    cache_read_tokens: cache,
    cost_usd: Number(((input + output * 4) / 1_000_000).toFixed(4)),
  })
  return {
    hourly: days <= 7 ? hours : null,
    recent: [
      row('17:02:41', 'claude-sonnet-5', 'subscription', 61_200, 830, 58_800),
      row('16:58:10', 'glm-4.6', 'gateway', 8_680_000, 41_300, 7_900_000),
      row('16:40:05', 'mimo-v2.6-pro', 'gateway', 412_000, 9_100, null),
    ],
  }
}

/** `usageStats()` 那一份，贴上它之外的那几块。 */
export function withUsageExtras(usage: Record<string, unknown>): Record<string, unknown> {
  const days = usage.days as number
  const dates = (usage.series as { date: string }[]).map((row) => row.date)
  return { ...usage, credits: creditsBurnout(), claude_accounts: claudePool(), ...hourlyAndRecent(days, dates) }
}
