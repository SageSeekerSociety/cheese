/**
 * proto-admin-fixtures/gateway.ts — 模型管理那两页的夹具：模型清单、空间用量、审计流水。
 *
 * 刻意放进几条「长内容」—— 很长的模型名、没定价的模型、被停用的模型 —— 布局要在这些行
 * 上也站得住，不能只在整齐的样例上好看。数据照 `backend/app/domain/gateway/` 的字段造。
 */
import type { GatewayAuditEntry, GatewayModelInfo, GatewayProject, GatewayUsageNumbers } from '@/api'

import { ago, DAY, usage } from './support'

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
    blocked_reasons: [],
    unpriced_reason: null,
    upstream: { model: name, host, provider: host.split('.')[0] },
    prices: { input: 3, output: 15, cache_read: 0.3, cache_creation: 3.75 },
    capabilities: { reasoning: true, vision: true },
    usage: u,
    series: [3, 5, 4, 8, 6, 9, 7].map((n) => n * (u.requests / 40)),
    overseas: false,
    ...extra,
  }
}

export const MODELS: GatewayModelInfo[] = [
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
    blocked_reasons: ['blocked'],
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

export const PROJECTS: GatewayProject[] = [
  project('a1b2c3d4e5', 'Cheese 平台代码', 402.1, null, 0),
  project('b2c3d4e5f6', '计算机系统基础 · 2026 秋（100 人课程空间的共享额度，名字很长的样例）', 188.4, 400, 9420),
  project('c3d4e5f6a7', '推荐算法原型', 61.5, 100, 3075),
  project('d4e5f6a7b8', '人大三方向资料调研', 34.9, 30, 1745),
]

export const AUDIT: GatewayAuditEntry[] = [
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
