/**
 * 后台布局预览用的样例数据：模型管理、空间申请两页（临时，只进预览构建）。
 *
 * 全部是编的：模型名、项目名、金额、申请人都不对应真实数据。刻意放进几条「长内容」
 * —— 很长的模型名、很长的申请简介、没定价的模型、被停用的模型 —— 布局要在这些行上
 * 也站得住，不能只在整齐的样例上好看。
 */
import type { GatewayAuditEntry, GatewayModelInfo, GatewayProject, GatewayUsageNumbers } from '@/api'
import type { SpaceApplication } from '@/network/api/spaces/types'

const DAY = 86400_000
const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
const day = (offset: number) => new Date(Date.now() - offset * DAY).toISOString().slice(0, 10)

function usage(spend: number, requests: number, tokens: number, failed = 0): GatewayUsageNumbers {
  return {
    spend_usd: spend,
    requests,
    failed_requests: failed,
    prompt_tokens: Math.round(tokens * 0.7),
    completion_tokens: Math.round(tokens * 0.2),
    cache_read_tokens: Math.round(tokens * 0.1),
    total_tokens: tokens,
  }
}

function model(
  name: string,
  label: string,
  host: string,
  u: GatewayUsageNumbers,
  extra: Partial<GatewayModelInfo> = {}
): GatewayModelInfo {
  return {
    name,
    model_id: name,
    label,
    origin: 'config',
    blocked: false,
    selectable: true,
    priced: true,
    offered: true,
    blocked_reason: null,
    unpriced_reason: null,
    upstream: { model: name, host, provider: host.split('.')[0] },
    prices: { input: 3, output: 15, cache_read: 0.3, cache_creation: 3.75 },
    capabilities: { reasoning: true, vision: true },
    usage: u,
    series: [3, 5, 4, 8, 6, 9, 7].map((n) => n * (u.requests / 40)),
    ...extra,
  }
}

const MODELS: GatewayModelInfo[] = [
  model('claude-opus-5-5', 'Claude Opus 5.5', 'api.anthropic.com', usage(412.36, 1840, 96_400_000, 3)),
  model('claude-sonnet-5', 'Claude Sonnet 5', 'api.anthropic.com', usage(188.02, 5210, 142_000_000, 11)),
  model('glm-4.6', 'GLM 4.6', 'open.bigmodel.cn', usage(21.7, 3380, 58_900_000, 26), {
    prices: { input: 0.7, output: 0.7 },
  }),
  model('gpt-5.2-codex', 'GPT-5.2 Codex', 'api.openai.com', usage(64.9, 902, 21_300_000, 0), { origin: 'runtime' }),
  model(
    'qwen3-coder-480b-a35b-instruct-long-context-preview-2026-09',
    'Qwen3 Coder 480B（长上下文预览版，名字很长的样例）',
    'dashscope.aliyuncs.com',
    usage(0, 12, 88_000, 4),
    { priced: false, offered: false, unpriced_reason: '上游没有公布价格', prices: {} }
  ),
  model('deepseek-v4', 'DeepSeek V4', 'api.deepseek.com', usage(0, 0, 0), {
    blocked: true,
    offered: false,
    blocked_reason: '管理员停用：上游频繁超时',
  }),
]

function project(id: string, name: string, spend: number, budget: number | null, used: number): GatewayProject {
  return {
    project_id: id,
    name,
    key_alias: `proj-${id.slice(0, 6)}`,
    has_key: true,
    gateway_spend_usd: spend,
    max_budget_usd: budget,
    budget_derived_usd: budget,
    budget_override_usd: null,
    credits: { total: 20000, used, remaining: 20000 - used, unlimited: budget === null },
    usage: usage(spend, Math.round(spend * 9), Math.round(spend * 230_000)),
  }
}

const PROJECTS: GatewayProject[] = [
  project('a1b2c3d4e5', 'Cheese 平台代码', 402.1, null, 0),
  project('b2c3d4e5f6', '计算机系统基础 · 2026 秋（100 人课程空间的共享额度，名字很长的样例）', 188.4, 400, 9420),
  project('c3d4e5f6a7', '推荐算法原型', 61.5, 100, 3075),
  project('d4e5f6a7b8', '人大三方向资料调研', 34.9, 30, 1745),
]

const AUDIT: GatewayAuditEntry[] = [
  {
    created_at: ago(2 * 3600_000),
    actor_handle: 'andy',
    action: 'model.block',
    target: 'deepseek-v4',
    result: 'ok',
    detail: '上游连续 3 小时超时率超过 20%，先停用，恢复后再上架。',
    before: { blocked: false },
    after: { blocked: true },
  },
  {
    created_at: ago(DAY),
    actor_handle: 'wangchangxin',
    action: 'project.budget',
    target: '计算机系统基础 · 2026 秋',
    result: 'ok',
    detail: null,
    before: { max_budget_usd: 300 },
    after: { max_budget_usd: 400 },
  },
  {
    created_at: ago(3 * DAY),
    actor_handle: 'andylizf',
    action: 'model.create',
    target: 'gpt-5.2-codex',
    result: 'failed',
    detail: '上游返回 400：model not found（样例错误）',
    before: null,
    after: null,
  },
]

function application(
  id: number,
  name: string,
  owner: string,
  intro: string,
  status: SpaceApplication['reviewStatus'],
  hoursAgo: number
): SpaceApplication {
  return {
    id,
    avatarId: null,
    name,
    intro,
    reviewStatus: status,
    description: intro,
    owner,
    reviewReason: status === 'REJECTED' ? '简介里没说明题目来源，补充后可重新提交。' : null,
    reviewedBy: status === 'PENDING' ? null : 'andy',
    reviewedAt: status === 'PENDING' ? null : ago(hoursAgo * 3600_000 - 3600_000),
    createdAt: ago(hoursAgo * 3600_000),
  }
}

const APPLICATIONS: SpaceApplication[] = [
  application(41, '算法竞赛周练', 'maxiaoyu', '每周一套题，赛后讲评。面向校队新人。', 'PENDING', 3),
  application(
    42,
    '计算机系统基础 2026 秋季学期实验题目板（含 4 个大作业与每周小测的全部题目，名字很长的样例）',
    'caisongyang',
    '本学期约 100 名学生使用。题目按周发布，每道题附评分脚本；助教 4 人负责批改。申请公开是为了让往届学生也能看到讲评。这段简介故意写得很长，用来看列表在长内容下会不会被撑坏。',
    'PENDING',
    20
  ),
  application(43, '数据库课程设计', 'chiruotong', '期末大作业选题与中期检查。', 'PENDING', 30),
  application(38, '前端组件练习', 'pengwenbo', '组件拆分与测试的练习题。', 'APPROVED', 72),
  application(36, '随便建建', 'n1ctheboy', '测试一下。', 'REJECTED', 96),
]

/** 返回 `undefined` 表示「不是这两页的接口」，交回给反馈那份假数据。 */
export function adminRoutes(path: string, method: string, url: URL): { data: unknown } | undefined {
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
  return undefined
}
