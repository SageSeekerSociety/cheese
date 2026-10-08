/**
 * 用量看板上那两块**不由用量表算出来**的假数据：额度燃尽、Claude 账号池。两者都是
 * `/admin/stats/usage` 那条路由在 `usageStats()` 之外**贴上去**的（真后端里这两块也各有
 * 各的来源：额度来自 grants，池子来自那台代理机器上的快照），所以单独放一处。
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
