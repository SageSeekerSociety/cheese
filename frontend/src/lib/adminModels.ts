// 模型管理那一页读到的三份响应的形状，和它们里面每一行的那几个判据。
//
// 为什么单独一份而不是留在页面里：拆开之后**取数**（`composables/useAdminModels.ts`）
// 和**画**（`components/admin/models/*.vue`）都要说同一件事的形状，放在任何一边都会让
// 另外一边反过来依赖它。和 `lib/adminStats.ts` 是同一个理由、同一个位置。
//
// 这里是**服务端契约的镜像**，不是本地模型：字段名与可空性照网关台账那三条接口下发的
// 写，这一层不重命名、不解释。
//
// 那几个 `displayName` / `originKey` / `statusQuiet` / `failRate` 是**行自己的判据**
// （一格该画成什么），不是某一段的画法：模型表里那条 diff 也会用到同一个口径，所以它
// 跟着形状走。真正只属于某一屏的东西（窗口文案、KPI 的拼装）留在 composable 里。

import { fmtPercent } from './usageFormat'

/** 一段窗口里的用量（契约 §3.1 的 `usage`）。三段各有一份，口径相同。 */
export interface ModelUsage {
  spend_usd: number
  requests: number
  failed_requests: number
  total_tokens: number
}

/** §3.1 的模型项。 */
export interface ModelRow {
  name: string
  label: string
  origin: string
  blocked: boolean
  selectable: boolean
  priced: boolean
  offered: boolean
  blocked_reason: string | null
  unpriced_reason: string | null
  upstream: { model: string; host: string | null; provider: string }
  prices: Record<string, number | null | undefined>
  capabilities: Record<string, boolean | undefined>
  usage: ModelUsage
  /** 行内 sparkline 的逐日 token（与详情折线同源同账）。 */
  series?: number[]
  /** 档位：方案按它限定可用的模型。缺省是 included。 */
  tier?: 'included' | 'premium' | 'frontier'
}

/** 网关这一侧的状态（契约 §2 的 `gateway` 那一块）。 */
export interface GatewayState {
  reachable: boolean
  readiness: string | null
  admin_configured: boolean
  detail: string | null
  fetched_at: string
}

/** `GET /model/info` 的响应。 */
export interface ModelsListing {
  gateway: GatewayState
  window: { days: number; start_date: string; end_date: string }
  totals: ModelUsage
  models: ModelRow[]
}

/** §3.4 的项目行。 */
export interface ProjectRow {
  project_id: string
  name: string
  key_alias: string
  has_key: boolean
  gateway_spend_usd: number
  max_budget_usd: number | null
  budget_derived_usd: number | null
  budget_override_usd: number | null
  credits: { total: number | null; used: number; remaining: number; unlimited: boolean }
  usage: ModelUsage
}

/** `GET /model/projects` 的响应。 */
export interface ProjectsPayload {
  projects: ProjectRow[]
  totals: { projects: number; with_key: number; over_budget: number; unlimited: number }
}

/** 一条审计记录。 */
export interface AuditItem {
  created_at: string
  actor_handle: string
  action: string
  target: string
  result: string
  detail: string | null
  /** 改动前后的字段快照（写入时已脱敏）。「查看改动」按钮与 diff 展开的数据。 */
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

/** 模型的显示名。`label` 缺失时退回 `name`（网关里的人给名字时才带 label）。 */
export function displayName(row: ModelRow): string {
  return row.label || row.name
}

/** 来源徽章的两态：配置文件 / 运行时新增。 */
export function originKey(row: ModelRow): string {
  return row.origin === 'config' ? 'models.table.origin.config' : 'models.table.origin.runtime'
}

/** 状态列在窄屏卡片里**整格收起来**的条件：这个窗口里没有请求时，
 *  那一格画的是一句「—」。卡片上多一行「状态 —」是没有信息的行，而真有事的那几行
 *  照样画得出来（同成员页「异常才说话」那条）。 */
export function statusQuiet(row: ModelRow): boolean {
  return !row.usage.requests
}

/** 状态列的失败率：0 请求画 `—`（「没用到」和「没失败」是两句话）；>5% 红、
 *  >0 琥珀、否则灰 —— 失败率是一个**例外状态**，正常时它不该抢眼。
 *
 *  `title` 那句在这里拼不了（它要 `t`），所以只回值与本行该用的色调，交给画的那一格。 */
export function failRate(row: ModelRow): { text: string; tone: string } {
  const { requests, failed_requests: failed } = row.usage
  if (!requests) return { text: '—', tone: 'amd__rate--muted' }
  const rate = fmtPercent(failed / requests)
  const tone = failed / requests > 0.05 ? 'amd__rate--danger' : failed > 0 ? 'amd__rate--warn' : 'amd__rate--muted'
  return { text: rate, tone }
}
