// Preview-only sample reports. No production API or account data is used.
import type { DocsAssistantReport, TaskNamingReport } from '@/views/admin/features/featureApi'

export function featureReport(path: string, url: URL): { data: unknown } | undefined {
  const days = Number(url.searchParams.get('days') ?? 7)
  const dates = Array.from({ length: days }, (_, index) =>
    new Date(Date.now() - (days - index - 1) * 86400000).toISOString().slice(0, 10)
  )
  const window = { days, start: dates[0], end: dates[dates.length - 1] }
  if (path === '/admin/feature-stats/task-naming') {
    const trend = dates.map((date, i) => ({ date, auto: 8 + (i % 5), person: i % 3 }))
    const auto = trend.reduce((sum, point) => sum + point.auto, 0)
    const person = trend.reduce((sum, point) => sum + point.person, 0)
    const data: TaskNamingReport = {
      ...window,
      id: 'task-naming',
      title: '智能命名',
      summary: '房间自动命名的调用与标题变更',
      numbers: {
        calls: { value: 485, failed: 2, success_rate: 483 / 485 },
        tokens: { value: 802900, prompt: 760000, completion: 42900, cache_read: 0 },
        cost: { usd: 0.2637, source: 'gateway', budget_usd: 5, budget_duration: '1d', key_spend_usd: 0.0151 },
        renames: { value: auto, name: auto - 12, calibrate: 8, follow: 4 },
        person_edits: { value: person },
        overridden: { value: 1, named: 36, share: 1 / 36 },
      },
      trend,
    }
    return { data }
  }
  if (path === '/admin/feature-stats/docs-assistant') {
    const data: DocsAssistantReport = {
      ...window,
      id: 'docs-assistant',
      title: '问芝士',
      summary: '文档站的问答助手',
      numbers: {
        visitors: { value: 480, logged_in: 240 },
        askers: { value: 120, share: 0.25 },
        questions: { value: 360, per_asker: 3 },
        answer_rate: { value: 0.9, answered: 324, total: 360 },
        cost: { usd: 1.28, per_question: 1.28 / 360, source: 'estimated', unpriced_tokens: 0 },
      },
      trend: dates.map((date, i) => ({ date, visitors: 40 + (i % 12), askers: 10 + (i % 5), questions: 20 + (i % 9) })),
      tokens: {
        count: 360,
        avg: 840,
        median: 720,
        min: 120,
        p90: 1600,
        max: 3200,
        histogram: [
          { from: 0, to: 500, count: 80 },
          { from: 500, to: 1000, count: 190 },
          { from: 1000, to: 3500, count: 90 },
        ],
      },
      latency: { count: 360, avg: 2400, median: 1800, min: 500, p90: 5200, max: 12000 },
      outcomes: { answered: 324, no_match: 30, failed: 6, total: 360 },
      unanswered: [{ question: '如何为项目配置独立预算？', page: '/docs/projects', count: 8 }],
    }
    return { data }
  }
  return undefined
}
