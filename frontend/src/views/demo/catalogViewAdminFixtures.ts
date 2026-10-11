/**
 * 后台那一组整页条目（`catalogViewAdmin.ts` + `catalogViewAdmin2.ts`）用的数据。
 *
 * 形状照产品里的契约写，不现编：方案与团队照 `/admin/plans`、`/admin/teams` 的回答
 * （`@/lib/adminCredits`），棘轮照 `/admin/ratchet` 的 `RatchetBoard`，运行记录照
 * `/admin/run-records` 的 `RunRecordOverview` / `RunRecordDetail`，协议照
 * `/legal/*` 的 `LegalDocumentFull`。看板那七份响应在 `catalogDashboardFixtures.ts`，
 * 这里不再抄一份（`AdminStatsPageView` 直接吃那七份）。
 *
 * 协议那份类型从承载它的页面组件 `$props` 里取，不从旧的网络层取：demo 源文件引用
 * `src/network` 会把 `legacy-network` 的账记上新的一笔。契约本就写在 `LegalDocumentView.vue` 上。
 */
import type { CreditAudit, CreditPack, CreditTeamDetail, CreditTeamPage, Plan } from '@/lib/adminCredits'
import type { RatchetBoard, RatchetCheck } from '@/views/admin/ratchetApi'
import type { RunRecordDetail, RunRecordGroup, RunRecordOverview } from '@/views/admin/runRecordsApi'
import type LegalDocumentView from '@/views/legal/LegalDocumentView.vue'

/** 一份完整的协议版本（`/legal/*` 的回答）。 */
type LegalDocumentFull = NonNullable<InstanceType<typeof LegalDocumentView>['$props']['doc']>

// --- 方案与额度 ------------------------------------------------------------

export const CRV_FREE: Plan = {
  key: 'free',
  name: 'Free',
  audience: 'both',
  credits_per_period: 125,
  period: 'month',
  windows: [],
  model_tiers: ['included'],
  unlimited: false,
  admin_only: false,
  rank: 0,
  team_count: 213,
  is_default: true,
}

export const CRV_RESERVE: Plan = {
  key: 'reserve',
  name: 'Reserve',
  audience: 'team',
  credits_per_period: null,
  period: 'month',
  windows: [],
  model_tiers: null,
  unlimited: true,
  admin_only: true,
  rank: 100,
  team_count: 1,
  is_default: false,
}

const CRV_CLASSROOM: Plan = {
  key: 'classroom',
  name: 'Classroom',
  audience: 'team',
  credits_per_period: null,
  period: 'month',
  windows: [
    { hours: 5, credits: 20 },
    { calendar: 'week', credits: 100 },
  ],
  model_tiers: ['included', 'premium'],
  unlimited: false,
  admin_only: false,
  rank: 10,
  team_count: 1,
  is_default: false,
}

export const CRV_PLANS: Plan[] = [CRV_FREE, CRV_RESERVE, CRV_CLASSROOM]

function crvPack(over: Partial<CreditPack>): CreditPack {
  return {
    id: 'p',
    source: 'plan_period',
    project_id: null,
    task_id: null,
    credits_total: 125,
    credits_used: 0,
    period_start: '2026-10-01T00:00:00+00:00',
    expires_at: '2026-10-31T23:59:59+00:00',
    reason: null,
    created_at: '2026-10-01T00:00:00+00:00',
    ...over,
  }
}

const CRV_LAB_PACKS: CreditPack[] = [
  crvPack({ id: 'p1', credits_used: 115 }),
  crvPack({
    id: 'p2',
    source: 'admin_grant',
    credits_total: 300,
    credits_used: 90,
    period_start: null,
    expires_at: '2026-12-31T15:59:59+00:00',
    reason: '期中项目展示前临时加量',
  }),
]

export const CRV_TEAMS: CreditTeamPage = {
  items: [
    {
      id: 1,
      name: 'Cheese 开发组',
      handle: 'cheese-dev',
      personal_owner: null,
      personal_owner_nickname: null,
      member_count: 9,
      plan_key: 'reserve',
      period: { start: '2026-10-01T00:00:00+00:00', credits_total: null, credits_used: 0 },
      packs: [],
    },
    {
      id: 2,
      name: '城市数据实验室',
      handle: 'citylab',
      personal_owner: null,
      personal_owner_nickname: null,
      member_count: 6,
      plan_key: 'free',
      period: { start: '2026-10-01T00:00:00+00:00', credits_total: 125, credits_used: 115 },
      packs: CRV_LAB_PACKS,
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
}

export const CRV_TEAM_DETAIL: CreditTeamDetail = {
  id: 2,
  name: '城市数据实验室',
  handle: 'citylab',
  personal_owner: null,
  personal_owner_nickname: null,
  member_count: 6,
  plan: CRV_FREE,
  period: { start: '2026-10-01T00:00:00+00:00', credits_total: 125, credits_used: 115 },
  packs: CRV_LAB_PACKS,
}

export const CRV_HISTORY: CreditAudit[] = [
  {
    created_at: '2026-10-12T06:05:00+00:00',
    actor_handle: 'starry',
    action: 'team.grant',
    target: '2',
    before: null,
    after: { credits_total: 300, expires_at: '2026-12-31T15:59:59+00:00' },
  },
]

export const CRV_TIER_MODELS = {
  included: ['deepseek-flash', 'glm-4.7'],
  premium: ['kimi-k3'],
  frontier: [],
}

// --- 棘轮 ------------------------------------------------------------------

/** 一道检查的两个点，都是 `pass`、都量到 3 —— 和 `AdminRatchetPageView.spec.ts`
 *  同一份形状（真实后端在这两个点上的读数就是这样）。 */
const RATCHET_CHECK: RatchetCheck = {
  id: 'fe-boundary',
  area: 'boundaries',
  better: 'down',
  direction: 'flat',
  status: 'pass',
  actual: 3,
  frozen: 3,
  stale: [],
  stale_count: null,
  rule_fingerprint: 'fp-a',
  points: [
    {
      commit: 'aaaaaaa1',
      collected_at: '2026-09-30T10:00:00+00:00',
      run_url: 'https://example.test/run/1',
      collection: 'ok',
      status: 'pass',
      actual: 3,
      frozen: 3,
      stale_count: null,
      rule_fingerprint: 'fp-a',
      rule_changed: false,
      new_exemptions: null,
      details: null,
      reason: null,
    },
    {
      commit: 'bbbbbbb2',
      collected_at: '2026-09-30T11:00:00+00:00',
      run_url: 'https://example.test/run/2',
      collection: 'ok',
      status: 'pass',
      actual: 3,
      frozen: 3,
      stale_count: null,
      rule_fingerprint: 'fp-a',
      rule_changed: false,
      new_exemptions: null,
      details: null,
      reason: null,
    },
  ],
}

export const CRV_RATCHET: RatchetBoard = {
  repo: 'SageSeekerSociety/cheese',
  generated_at: '2026-09-30T13:00:00Z',
  deployed_commit: 'dc069a4aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  collected_commit: 'bbbbbbb2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  collected_at: '2026-09-30T11:00:00+00:00',
  run_url: 'https://example.test/run/2',
  collection: 'ok',
  points: 2,
  total_stored: 2,
  collections: [
    {
      commit: 'aaaaaaa1',
      collected_at: '2026-09-30T10:00:00+00:00',
      run_url: 'https://example.test/run/1',
      collection: 'ok',
      reason: null,
    },
    {
      commit: 'bbbbbbb2',
      collected_at: '2026-09-30T11:00:00+00:00',
      run_url: 'https://example.test/run/2',
      collection: 'ok',
      reason: null,
    },
  ],
  areas: [{ area: 'boundaries', checks: [RATCHET_CHECK] }],
}

// --- 运行记录 --------------------------------------------------------------

function runGroup(kind: string, key: string, title: string, over: Partial<RunRecordGroup> = {}): RunRecordGroup {
  return {
    key,
    kind,
    severity: kind.endsWith('error') ? 'error' : 'info',
    title,
    count: 3,
    projects: 2,
    first_at: '2026-10-06T09:00:00Z',
    last_at: '2026-10-06T10:00:00Z',
    buckets: Array.from({ length: 24 }, (_, i) => (i === 9 ? 3 : 0)),
    ...over,
  }
}

export const CRV_RUN_OVERVIEW: RunRecordOverview = {
  window: '24h',
  since: '2026-10-05T10:00:00Z',
  totals: { error_kinds: 1, errors: 3, recovered: 3 },
  groups: [
    runGroup('backend_error', 'fp-site', 'GET /api/site 超时'),
    runGroup('turn_failed', 'turn', '对话里的一轮没跑完', { severity: 'error', count: 1 }),
    runGroup('api_retry', 'retry', '接口重试后成功'),
  ],
}

export const CRV_RUN_DETAIL: RunRecordDetail = {
  kind: 'backend_error',
  severity: 'error',
  content: 'GET /api/site 超时：上游 30 秒没有回应',
  meta: { where: 'GET /api/site', request_id: 'req-9f2a' },
  at: '2026-10-06T10:00:00Z',
  places: [
    { project_id: 'p-1', project: '知是平台', conversation_id: 't-9', at: '2026-10-06T10:00:00Z' },
    { project_id: 'p-2', project: '城西社区', conversation_id: 't-4', at: '2026-10-06T09:00:00Z' },
  ],
  more_places: 3,
}

// --- 协议 ------------------------------------------------------------------

export const CRV_LEGAL: LegalDocumentFull = {
  document: 'privacy',
  title: '隐私政策',
  version: '2026-01-01',
  effectiveDate: '2026-01-01',
  current: true,
  content: [
    '隐私政策',
    '',
    '我们会收集必要的信息来提供知是的服务，并按照下面的约定处理它们。',
    '',
    '## 我们收集什么',
    '',
    '- 你填写的账号信息',
    '- 你在平台里产生的内容',
  ].join('\n'),
  sha256: '0'.repeat(64),
}
