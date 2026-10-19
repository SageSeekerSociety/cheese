/**
 * proto-admin-fixtures/routes.ts — 预览那个假后端的后台路由：路径加方法进来，回什么由它
 * 定；认不出来的交回给反馈那份假数据（`featureReport`）。
 *
 * 出口只有 `adminRoutes`，由上一层的 barrel 重发出去 —— 名字和原来一模一样，
 * `proto-feedback-fixtures/routes.ts` 那边一行都不用改。
 */
import type { GatewayUsageNumbers } from '@/api'

import { AUDIT, MODELS, PROJECTS } from './gateway'
import { APPLICATIONS } from './spaces'
import { ago, day, usage } from './support'

import { featureReport } from '@/proto-feature-fixtures'

/** 返回 `undefined` 表示「不是这几页的接口」，交回给反馈那份假数据。`payload` 是
 *  已经解析过的请求体（只有写接口用得到）。 */
export function adminRoutes(
  path: string,
  method: string,
  url: URL,
  payload: Record<string, unknown> = {}
): { data: unknown } | undefined {
  if (path === '/admin/gateway/models' && method === 'GET') {
    const days = Number(url.searchParams.get('days') ?? 7)
    const totals = MODELS.reduce(
      (sum, m) => {
        for (const key of Object.keys(sum) as (keyof GatewayUsageNumbers)[]) sum[key] += m.usage[key]
        return sum
      },
      usage(0, 0, 0)
    )
    return {
      data: {
        gateway: { reachable: true, readiness: 'ok', admin_configured: true, detail: null, fetched_at: ago(60_000) },
        window: { days, start_date: day(days - 1), end_date: day(0) },
        totals,
        models: MODELS,
      },
    }
  }
  if (path === '/admin/gateway/projects' && method === 'GET') {
    const days = Number(url.searchParams.get('days') ?? 7)
    return {
      data: {
        window: { days, start_date: day(days - 1), end_date: day(0) },
        projects: PROJECTS,
        totals: { projects: PROJECTS.length, with_key: PROJECTS.length, over_budget: 1, unlimited: 1 },
      },
    }
  }
  if (path === '/admin/gateway/audit' && method === 'GET') return { data: { items: AUDIT } }
  if (path === '/admin/spaces' && method === 'GET') {
    const status = (url.searchParams.get('status') ?? 'PENDING').toUpperCase()
    return { data: { items: APPLICATIONS.filter((a) => a.reviewStatus === status) } }
  }
  if (/^\/admin\/spaces\/\d+\/review$/.test(path) && method === 'POST') {
    // 通过 / 驳回：预览里把这一条改在样例上，好让「点了以后那一行去哪了」也是真的
    // （待审 → 已通过）。审查意见跟着一起落，驳回时那一栏才有话可看。
    const id = Number(path.split('/')[3])
    const row = APPLICATIONS.find((a) => a.id === id)
    if (!row) return undefined
    row.reviewStatus = payload.approved ? 'APPROVED' : 'REJECTED'
    row.reviewReason = typeof payload.reason === 'string' && payload.reason ? payload.reason : null
    row.reviewedBy = 'andy'
    row.reviewedAt = new Date().toISOString()
    return { data: { item: row } }
  }
  // 功能数据的目录。**只有 id + 标题 + 一句话**，一个数字都没有 —— 那是它的设计
  // （数字在各自的功能页上），不是还没做。值照抄服务端注册表
  // （`backend/app/domain/feature_stats/features/docs_assistant.py`）。
  if (path === '/admin/feature-stats' && method === 'GET') {
    return {
      data: {
        features: [
          { id: 'docs-assistant', title: '问芝士', summary: '文档站的问答助手' },
          { id: 'task-naming', title: '智能命名', summary: '任务自动命名的调用与标题变更' },
        ],
      },
    }
  }
  return method === 'GET' ? featureReport(path, url) : undefined
}
