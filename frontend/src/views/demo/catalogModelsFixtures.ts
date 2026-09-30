/**
 * 模型管理那几件（`components/admin/models/*.vue`）在预览站里吃的数据。
 *
 * 形状**不是编的**：三份响应按 `@/lib/adminModels` 标了类型，少一个键、多一个键都在
 * `vue-tsc` 那里当场红。值照网关台账那三条接口的形状写，只把长列表截短（逐日 series
 * 留七个点、模型留四个、项目留四个、审计留三条）。
 *
 * 为什么单独一份文件：`catalogFixtures.ts` 已经六百多行，三份响应塞进去会顶到
 * `frontend/src` 那一千行的上限；和 `catalogDashboardFixtures.ts` 同一个理由。
 *
 * 为什么这六件能在预览站里单独画：它们是「只吃 props」的那种组件（`frontend_grade.py`
 * 的 A 级，审计那件带 `UserRefLink`、是 C 级），取数全在
 * `composables/useAdminModels.ts` 里 —— 所以这里给的就是一份现成的 props，不用起假后端。
 */
import type { AuditItem, ModelsListing, ModelUsage, ProjectsPayload } from '@/lib/adminModels'

/** 一段窗口里的用量。四个数一起给，免得每一行写四遍。 */
function usage(spend: number, requests: number, failed: number, tokens: number): ModelUsage {
  return { spend_usd: spend, requests, failed_requests: failed, total_tokens: tokens }
}

/** 窗口：过去 7 天。`/model/info` 与 `/model/projects` 各带一份，值一样。 */
const WINDOW = { days: 7, start_date: '2026-09-23', end_date: '2026-09-30' }

/** 网关活着、管理密钥也配了。 */
const GATEWAY_OK = {
  reachable: true,
  readiness: 'ready',
  admin_configured: true,
  detail: '网关在运行，管理密钥已配。',
  fetched_at: '2026-09-30T06:12:00Z',
}

/** 读不到网关的那一份：`models` 一个都没有，`detail` 是**服务端的原话**。 */
const GATEWAY_DOWN_DETAIL = 'connect ECONNREFUSED 10.0.0.4:4000'

// --- 页头 ------------------------------------------------------------------

/** 页头那一组 props。`health` 是 composable 算好的形状，这里直接给。 */
export function modelsHeaderProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sub: '网关上的模型、单价与每个项目的额度 · 2026-09-23 – 2026-09-30',
    health: { ok: true, text: 'ready · 3 小时前', title: '网关在运行，管理密钥已配。' },
    days: 7,
    windows: [7, 14, 30],
    loading: false,
    ...over,
  }
}

// --- GET /model/info --------------------------------------------------------

/** 四个模型，四种该被看见的样子：常开的、config 只读的、停用的、没定价的。 */
export const MODELS_LISTING: ModelsListing = {
  gateway: GATEWAY_OK,
  window: WINDOW,
  totals: usage(12.5, 128, 3, 2_412_900),
  models: [
    {
      name: 'glm-4.7',
      label: 'GLM 4.7',
      origin: 'runtime',
      blocked: false,
      selectable: true,
      priced: true,
      offered: true,
      blocked_reason: null,
      unpriced_reason: null,
      upstream: { model: 'anthropic/glm-4.7', host: 'open.bigmodel.cn', provider: 'anthropic' },
      prices: { input: 1e-6, output: 2e-6 },
      capabilities: { reasoning: true, tools: true },
      usage: usage(9.25, 96, 1, 1_820_400),
      series: [120, 180, 90, 240, 310, 260, 420],
    },
    {
      name: 'text-embedding-3-large',
      label: '向量（大）',
      origin: 'config',
      blocked: false,
      selectable: true,
      priced: true,
      offered: true,
      blocked_reason: null,
      unpriced_reason: null,
      upstream: { model: 'openai/text-embedding-3-large', host: 'api.openai.com', provider: 'openai' },
      prices: { input: 1.3e-7, output: 0 },
      capabilities: {},
      usage: usage(0.4, 22, 0, 512_000),
      series: [0, 40, 60, 30, 80, 90, 70],
    },
    {
      name: 'gpt-5-mini',
      label: 'GPT-5 mini',
      origin: 'runtime',
      blocked: true,
      selectable: false,
      priced: true,
      offered: false,
      blocked_reason: '上游连续 5 次 401，已由运维停用。',
      unpriced_reason: null,
      upstream: { model: 'openai/gpt-5-mini', host: 'api.openai.com', provider: 'openai' },
      prices: { input: 2.5e-7, output: 1e-6 },
      capabilities: { tools: true },
      usage: usage(0.15, 6, 4, 33_500),
      series: [10, 8, 6, 4, 2, 2, 1],
    },
    {
      name: 'qwen3-local',
      label: '',
      origin: 'runtime',
      blocked: false,
      selectable: false,
      priced: false,
      offered: false,
      blocked_reason: null,
      unpriced_reason: '缺输出单价，问过网关的价目表，它没有这一档。',
      upstream: { model: 'qwen3', host: null, provider: 'openai-compatible' },
      prices: { input: 0 },
      capabilities: { reasoning: true },
      usage: usage(0, 0, 0, 0),
      series: [],
    },
  ],
}

/** 网关在、一个模型都没有（`table.empty` 那一格）。 */
export const MODELS_EMPTY: ModelsListing = {
  gateway: GATEWAY_OK,
  window: WINDOW,
  totals: usage(0, 0, 0, 0),
  models: [],
}

/** 读不到网关：`reachable: false`，页面据此把表体换成那条说明。 */
export const MODELS_UNREACHABLE: ModelsListing = {
  gateway: {
    reachable: false,
    readiness: null,
    admin_configured: true,
    detail: GATEWAY_DOWN_DETAIL,
    fetched_at: '2026-09-30T06:12:00Z',
  },
  window: WINDOW,
  totals: usage(0, 0, 0, 0),
  models: [],
}

/** 那一格说明要的 props：网关读不出来时表格自己画的那句话。 */
export const MODELS_TABLE_BASE = {
  models: null,
  loading: false,
  state: 'rows' as const,
  error: null,
  gatewayDetail: '读不到网关的模型清单。确认网关在运行，然后重试。',
}

// --- GET /model/projects ----------------------------------------------------

/** 四个项目，四种刹车值该被看见的样子：有覆盖的、折出来的、没设的、不限量的。 */
export const MODELS_PROJECTS: ProjectsPayload = {
  totals: { projects: 4, with_key: 3, over_budget: 1, unlimited: 1 },
  projects: [
    {
      project_id: 'p-cheese',
      name: '知是平台后端',
      key_alias: 'cheese-prod',
      has_key: true,
      gateway_spend_usd: 54.2,
      max_budget_usd: 50,
      budget_derived_usd: 20,
      budget_override_usd: 50,
      credits: { total: 100, used: 45.8, remaining: 54.2, unlimited: false },
      usage: usage(54.2, 512, 2, 9_200_000),
    },
    {
      project_id: 'p-course',
      name: '课程资料整理',
      key_alias: 'course-dev',
      has_key: true,
      gateway_spend_usd: 3.75,
      max_budget_usd: 20,
      budget_derived_usd: 20,
      budget_override_usd: null,
      credits: { total: 20, used: 3.75, remaining: 16.25, unlimited: false },
      usage: usage(3.75, 40, 0, 610_000),
    },
    {
      project_id: 'p-handbook',
      name: '慢病管理手册',
      key_alias: 'handbook',
      has_key: true,
      gateway_spend_usd: 0,
      max_budget_usd: null,
      budget_derived_usd: null,
      budget_override_usd: null,
      credits: { total: 10, used: 0, remaining: 10, unlimited: false },
      usage: usage(0, 0, 0, 0),
    },
    {
      project_id: 'p-demo',
      name: '演示项目',
      key_alias: 'demo',
      has_key: false,
      gateway_spend_usd: 0,
      max_budget_usd: null,
      budget_derived_usd: null,
      budget_override_usd: null,
      credits: { total: null, used: 0, remaining: 0, unlimited: true },
      usage: usage(0, 0, 0, 0),
    },
  ],
}

/** 一个项目都没有（`budget.empty` 那一格）。 */
export const MODELS_PROJECTS_EMPTY: ProjectsPayload = {
  totals: { projects: 0, with_key: 0, over_budget: 0, unlimited: 0 },
  projects: [],
}

// --- GET /model/audit -------------------------------------------------------

/** 三条记录：改成功带快照的、改失败带原话的、没快照可 diff 的成功项。 */
export const MODELS_AUDIT: AuditItem[] = [
  {
    created_at: '2026-09-30T06:04:00Z',
    actor_handle: 'alice',
    action: 'model.update',
    target: 'glm-4.7',
    result: 'ok',
    detail: null,
    before: { label: 'GLM 4.7', priced: true, prices: { input: 1e-6, output: 2e-6 }, blocked: false },
    after: { label: 'GLM 4.7（旗舰）', priced: true, prices: { input: 1.2e-6, output: 2.4e-6 }, blocked: false },
  },
  {
    created_at: '2026-09-30T05:12:00Z',
    actor_handle: 'bob',
    action: 'model.delete',
    target: 'gpt-4o',
    result: 'failed',
    detail: '网关拒绝了这次删除：这个模型正被 2 个项目路由着。',
    before: null,
    after: null,
  },
  {
    created_at: '2026-09-29T22:40:00Z',
    actor_handle: 'alice',
    action: 'project.budget',
    target: '知是平台后端',
    result: 'ok',
    detail: null,
    before: null,
    after: null,
  },
]

// --- 那一组 props -----------------------------------------------------------

/** 模型表那一段的 props（`models` 之外都给好了）。 */
export function modelsTableProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...MODELS_TABLE_BASE, models: MODELS_LISTING, ...over }
}

/** 额度那一段的 props。 */
export function modelsBudgetsProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { projects: MODELS_PROJECTS, loading: false, state: 'rows', error: null, ...over }
}

/** 最近操作那一段的 props。 */
export function modelsAuditProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { items: MODELS_AUDIT, loading: false, error: null, expanded: new Set<number>(), ...over }
}
