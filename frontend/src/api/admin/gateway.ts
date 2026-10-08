import { request } from '../http'

/* ---- 管理端（网关模型）----
 *
 * 后台「模型管理」那一页的九条接口（`backend/app/api/routes/admin_models.py`）。全是平台
 * 管理员的接口，网关侧的失败在服务端已经折成 503（不可达）/ 502（被拒），原因放在
 * `detail` 里 —— `request` 会把 `error.message` 原样带出来，页面要显示的正是服务端那句
 * 中文原因，所以这里不改写、不吞异常。
 *
 * 和看板那几条同一条纪律：`days` 是**页面**问的问题（窗口由人选的），由调用方给，不写死这里。
 */

/** 网关这一刻的状态。`admin_configured` 为 false 是「这个部署没配管理密钥」，不是
 *  「网关挂了」—— 页面上这两句话得分开说。 */
export interface GatewayStatus {
  reachable: boolean
  readiness: string | null
  admin_configured: boolean
  detail: string | null
  fetched_at: string | null
}

/** 用量窗口。`end_date` 当天**含**在内（闭区间，实测见契约 §0）。 */
export interface GatewayWindow {
  days: number
  start_date: string
  end_date: string
}

/** 一个模型在一个窗口里的用量。`/model/info` 里找不到它时**全 0**，不是 null。 */
export interface GatewayUsageNumbers {
  spend_usd: number
  requests: number
  failed_requests: number
  prompt_tokens: number
  completion_tokens: number
  cache_read_tokens: number
  total_tokens: number
}

/** 单价，单位是**每 token**（网关就是这么记的，页面负责 ×1e6 那类换算）。
 *  缺的键不出现 —— 「没价」和「0 价」在页面上是两回事。 */
export interface GatewayPrices {
  input?: number | null
  output?: number | null
  cache_read?: number | null
  cache_creation?: number | null
}

export type GatewayCapabilities = Partial<
  Record<'reasoning' | 'vision' | 'adaptive_thinking' | 'mid_conversation_system', boolean>
>

export interface GatewayUpstream {
  model: string
  host: string
  provider: string
}

/** 清单里的一个模型（契约 §3.1）。
 *
 *  `origin` 决定它是只读还是可改：`config` 来自 config.yaml、页面上只读；`runtime` 是
 *  网关里新增的，可改可删可停用。`offered = selectable && priced && !blocked`，是选择器
 *  真正会给出的那些；`blocked_reasons` 是没上架的原因码，`unpriced_reason` 是网关的说明。 */
export interface GatewayModelInfo {
  name: string
  model_id: string
  label: string
  origin: 'config' | 'runtime'
  blocked: boolean
  selectable: boolean
  priced: boolean
  offered: boolean
  blocked_reasons: string[]
  unpriced_reason: string | null
  upstream: GatewayUpstream
  prices: GatewayPrices
  capabilities: GatewayCapabilities
  usage: GatewayUsageNumbers
  /** 仅 origin=config 时给出：可复制的 config.yaml 片段（页面「怎么改」那一段）。 */
  config_yaml?: string
  /** 行内 sparkline 的逐日 token（与详情折线同源同账）；窗口内没用过是逐日 0。 */
  series?: number[]
}

export interface GatewayModelsPayload {
  gateway: GatewayStatus
  window: GatewayWindow
  totals: GatewayUsageNumbers
  models: GatewayModelInfo[]
}

/** 详情（契约 §3.2）。`series` 是这条模型每天的花费；`platform_usage` 是平台侧归因的
 *  读数，**以网关账本为准**（`resource_usage.by_model` 对网关流量不可信，见契约 §0）。 */
export interface GatewayModelDetail {
  model: GatewayModelInfo
  series: { date: string; spend_usd: number; requests: number; tokens: number }[]
  platform_usage: {
    calls: number
    tokens: number
    cost_usd: number
    unpriced_tokens: number
    note: string
  }
}

/** 新增一个运行时模型（契约 §3.3）。`api_key` 只在请求体里出现，**绝不回显**。 */
export interface GatewayModelCreateInput {
  name: string
  upstream_model: string
  api_base?: string | null
  api_key?: string | null
  label?: string
  selectable?: boolean
  prices?: GatewayPrices
  capabilities?: GatewayCapabilities
}

/** 改一个运行时模型（契约 §3.3）。PATCH 语义：只传要改的字段，缺的表示「别动它」。
 *
 *  `api_key_unchanged` 是「编辑界面不回显、也不拿空串覆盖上游凭据」那条路：界面不知道
 *  现在的 key，所以它要么给一个新的 `api_key`，要么声明「不改动」—— 不能发一个空串，
 *  那会把已存的凭据抹掉。 */
export interface GatewayModelUpdateInput {
  upstream_model?: string
  api_base?: string | null
  api_key?: string | null
  api_key_unchanged?: boolean
  label?: string
  selectable?: boolean
  prices?: GatewayPrices
  capabilities?: GatewayCapabilities
}

export interface GatewayProjectCredits {
  total: number
  used: number
  remaining: number
  unlimited: boolean
}

/** 额度段里的一个项目（契约 §3.4）。
 *
 *  `budget_derived_usd` 是按算力额度折出的刹车值（unlimited 时为 null）；
 *  `budget_override_usd` 是网关 key 上实际设的、与 derived 不一致的那个值 —— 两个都在，
 *  才看得出「这个项目的额度是不是被人手动改过」。 */
export interface GatewayProject {
  project_id: string
  name: string
  key_alias: string
  has_key: boolean
  gateway_spend_usd: number
  max_budget_usd: number | null
  budget_derived_usd: number | null
  budget_override_usd: number | null
  credits: GatewayProjectCredits
  usage: GatewayUsageNumbers
}

export interface GatewayProjectsPayload {
  window: GatewayWindow
  projects: GatewayProject[]
  totals: { projects: number; with_key: number; over_budget: number; unlimited: number }
}

/** 最近操作里的一行（契约 §3.5）。**失败的写操作也落行**（`result="failed"`），所以这段
 *  同时是「谁改了什么」和「哪一次没成」—— 少了失败那半，页面对「改不动」是无痕的。 */
export interface GatewayAuditEntry {
  created_at: string
  actor_handle: string
  action: string
  target: string
  result: 'ok' | 'failed'
  detail: string | null
  /** 改动前后的字段快照（写入时已脱敏）。两者都为空时这项操作没有可展示的字段变化。 */
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

export interface GatewayAuditPayload {
  items: GatewayAuditEntry[]
}

/** 这一节的查询串：`days` / `limit` 都是数字，统一走 URLSearchParams 编码。 */
function gatewayQuery(params: Record<string, number>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) search.set(key, String(value))
  return `?${search.toString()}`
}

export function getGatewayModels(days: number): Promise<GatewayModelsPayload> {
  return request<GatewayModelsPayload>(`/admin/gateway/models${gatewayQuery({ days })}`)
}

export function getGatewayModel(name: string, days: number): Promise<GatewayModelDetail> {
  return request<GatewayModelDetail>(`/admin/gateway/models/${encodeURIComponent(name)}${gatewayQuery({ days })}`)
}

export function createGatewayModel(body: GatewayModelCreateInput): Promise<{ model: GatewayModelInfo }> {
  return request<{ model: GatewayModelInfo }>('/admin/gateway/models', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateGatewayModel(name: string, body: GatewayModelUpdateInput): Promise<{ model: GatewayModelInfo }> {
  return request<{ model: GatewayModelInfo }>(`/admin/gateway/models/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteGatewayModel(name: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/admin/gateway/models/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export function setGatewayModelBlocked(name: string, blocked: boolean): Promise<{ blocked: boolean }> {
  return request<{ blocked: boolean }>(`/admin/gateway/models/${encodeURIComponent(name)}/blocked`, {
    method: 'POST',
    body: JSON.stringify({ blocked }),
  })
}

export function getGatewayProjects(days: number): Promise<GatewayProjectsPayload> {
  return request<GatewayProjectsPayload>(`/admin/gateway/projects${gatewayQuery({ days })}`)
}

/** 设或清一个项目的额度上限（`null` = 清除覆盖）。服务端立刻落到网关，回来的是**同一项**
 *  更新后的样子，页面拿它替换那一行即可。 */
export function setGatewayProjectBudget(projectId: string, maxBudgetUsd: number | null): Promise<GatewayProject> {
  return request<GatewayProject>(`/admin/gateway/projects/${encodeURIComponent(projectId)}/budget`, {
    method: 'PUT',
    body: JSON.stringify({ max_budget_usd: maxBudgetUsd }),
  })
}

export function getGatewayAudit(limit: number): Promise<GatewayAuditPayload> {
  return request<GatewayAuditPayload>(`/admin/gateway/audit${gatewayQuery({ limit })}`)
}
