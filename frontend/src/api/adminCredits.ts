// 方案与额度（`/admin/plans`、`/admin/teams`，#2397）。全部要平台管理员；每一次写都在
// 服务端落一行操作记录。
import type {
  CreditAudit,
  CreditPack,
  CreditTeamDetail,
  CreditTeamPage,
  GrantInput,
  ModelTier,
  Plan,
  PlanInput,
  TeamKind,
} from '@/lib/adminCredits'

import { request } from '../api'

export function listPlanModels(): Promise<{ models: { id: string; label: string; tier: ModelTier }[] }> {
  return request('/admin/plans/models')
}

export function listPlans(): Promise<{ plans: Plan[] }> {
  return request<{ plans: Plan[] }>('/admin/plans')
}

export function createPlan(body: PlanInput): Promise<Plan> {
  return request<Plan>('/admin/plans', { method: 'POST', body: JSON.stringify(body) })
}

/** 改动从下一期起生效，本期已发的额度不变。 */
export function updatePlan(key: string, body: Partial<PlanInput>): Promise<Plan> {
  return request<Plan>(`/admin/plans/${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify(body) })
}

/** 只能删没有团队在用的方案；新团队默认的方案不能删。 */
export function deletePlan(key: string): Promise<{ key: string }> {
  return request<{ key: string }>(`/admin/plans/${encodeURIComponent(key)}`, { method: 'DELETE' })
}

export function listCreditTeams(params: {
  q?: string
  plan?: string
  kind?: TeamKind
  page: number
  pageSize: number
}): Promise<CreditTeamPage> {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.plan) search.set('plan', params.plan)
  if (params.kind) search.set('kind', params.kind)
  search.set('page', String(params.page))
  search.set('page_size', String(params.pageSize))
  return request<CreditTeamPage>(`/admin/teams?${search.toString()}`)
}

export function getCreditTeam(teamId: number): Promise<CreditTeamDetail> {
  return request<CreditTeamDetail>(`/admin/teams/${teamId}`)
}

export function getCreditTeamHistory(teamId: number, limit = 50): Promise<{ items: CreditAudit[] }> {
  return request<{ items: CreditAudit[] }>(`/admin/teams/${teamId}/history?limit=${limit}`)
}

/** 方案的适用对象与团队不符时服务端拒绝（400，原话里带方案名）。 */
export function setCreditTeamPlan(teamId: number, planKey: string): Promise<{ team_id: number; plan_key: string }> {
  return request<{ team_id: number; plan_key: string }>(`/admin/teams/${teamId}/plan`, {
    method: 'PUT',
    body: JSON.stringify({ plan_key: planKey }),
  })
}

/** 改一条模型的档位：走模型管理同一条更新接口（只带 `tier`，别的字段不动）。
 *  配置文件里来的模型改不了，服务端会拒绝。 */
export function setGatewayModelTier(name: string, tier: ModelTier): Promise<unknown> {
  return request<unknown>(`/admin/gateway/models/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify({ tier }),
  })
}

export function grantTeamCredits(teamId: number, body: GrantInput): Promise<CreditPack> {
  return request<CreditPack>(`/admin/teams/${teamId}/grants`, { method: 'POST', body: JSON.stringify(body) })
}
