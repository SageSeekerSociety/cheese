/**
 * 后台那一组整页条目（`catalogViewAdmin.ts` + `catalogViewAdmin2.ts`）用的数据。
 *
 * 形状照产品里的契约写，不现编：方案与团队照 `/admin/plans`、`/admin/teams` 的回答
 * （`@/lib/adminCredits`），运行记录照
 * `/admin/run-records` 的 `RunRecordOverview` / `RunRecordDetail`，协议照
 * `/legal/*` 的 `LegalDocumentFull`。看板那七份响应在 `catalogDashboardFixtures.ts`，
 * 这里不再抄一份（`AdminStatsPageView` 直接吃那七份）。
 *
 * 协议那份类型从承载它的页面组件 `$props` 里取，不从旧的网络层取：demo 源文件引用
 * `src/network` 会把 `legacy-network` 的账记上新的一笔。契约本就写在 `LegalDocumentView.vue` 上。
 */
import type { CreditAudit, CreditPack, CreditTeamDetail, CreditTeamPage, Plan } from '@/lib/adminCredits'
import type { CiBoard, CiJobRow, CiStepRow } from '@/views/admin/ciActivityApi'
import type { MachinesOverview } from '@/views/admin/machinesApi'
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
      overseas_approved: true,
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
      overseas_approved: false,
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
  overseas_approved: false,
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

/** 机器页：一台在用（点开有两个沙箱，一个在等宿主机）、一台失联、一台被 provider 弄坏
 *  已放弃；过去一天起了 14 个沙箱，有一个没准备好就放弃了。 */
export const CRV_MACHINES: MachinesOverview = {
  configured: true,
  now: '2026-10-12T10:00:00Z',
  pool: {
    hosts: 2,
    vms: 0,
    max_hosts: 10,
    at_cap: false,
    slots: 10,
    used: 2,
    free: 8,
    min_free: 2,
    waiting: 1,
    oldest_wait_since: '2026-10-12T09:57:00Z',
    provider_failures: 1,
    provider_failure_limit: 3,
    provider_window_min: 60,
    provider_probe_min: 5,
    last_provider_failure: '2026-10-12T09:40:00Z',
    refusing: false,
    fixed_hosts: 10,
    warm_target: 0,
  },
  config: {
    slots_per_core: 2,
    sandbox_memory_mb: 3072,
    sandbox_idle_s: 600,
    host_idle_hold_s: 1800,
    rebuild_after_days: 14,
    vm_idle_s: 1800,
    sandbox_disk_budget_gb: 12,
    sandbox_inode_budget: 600000,
    near_cap: 0.8,
    check_s: 60,
    alert_used: { disk: 0.85, inodes: 0.9, memory: 0.9 },
    history_step_s: 300,
  },
  hosts: [
    {
      id: 'h-a',
      hostname: 'cheese-pool-7f3a',
      machine_id: 412,
      whole_machine: false,
      project: null,
      phase: 'ready',
      status: 'running',
      online: true,
      ip: '10.0.3.21',
      cores: 4,
      memory_mb: 16384,
      disk_gb: 40,
      slots: 5,
      used: 2,
      created_at: '2026-10-12T07:12:00Z',
      enrolled_at: '2026-10-12T07:15:00Z',
      last_seen_at: '2026-10-12T09:59:00Z',
      idle_since: null,
      offline_since: null,
      failed_at: null,
      released_at: null,
      image_version: '9f2c41d7a3b8e610',
      image_outdated: false,
      draining: false,
      draining_since: null,
      enroll_attempts: 1,
      enroll_error: null,
      usage: {
        read_at: '2026-10-12T09:59:20Z',
        disk_bytes: 40e9,
        disk_used_bytes: 35.2e9,
        inodes: 2_621_440,
        inodes_used: 610_000,
        memory_bytes: 16 * 2 ** 30,
        memory_used_bytes: 7.4 * 2 ** 30,
        swap_bytes: 8 * 2 ** 30,
        swap_used_bytes: 0.9 * 2 ** 30,
        memory_pressure_some_avg10: 2.4,
        memory_pressure_some_avg60: 1.1,
        memory_pressure_full_avg10: 0.2,
        memory_pressure_full_avg60: 0.1,
        zswap: false,
        cpu_busy: 0.31,
        load1: 1.6,
        sandboxes: 1,
        memory_history: [0.3, 0.34, 0.41, null, 0.45, 0.46],
        cpu_history: [0.1, 0.22, 0.35, null, 0.28, 0.31],
      },
      sandboxes: [
        {
          id: 's-1',
          spec: 'standard',
          state: 'working',
          project_id: 'p-1',
          project: '知是',
          room_id: 'r-1',
          room: '发布准备',
          placed_at: '2026-10-12T09:10:00Z',
          waiting_since: null,
          active_at: '2026-10-12T09:59:30Z',
          stopped_at: null,
          idle_deadline: null,
          usage: {
            memory_bytes: 2.6 * 2 ** 30,
            memory_peak_bytes: 2.9 * 2 ** 30,
            anon_bytes: 2.3 * 2 ** 30,
            file_bytes: 0.3 * 2 ** 30,
            swap_bytes: 0,
            oom_kills: 0,
            memory_read_at: '2026-10-12T09:59:20Z',
            disk_bytes: 4.2e9,
            disk_inodes: 130_000,
            disk_read_at: '2026-10-12T09:56:00Z',
          },
        },
        {
          id: 's-2',
          spec: 'standard',
          state: 'waiting',
          project_id: 'p-2',
          project: 'YOJ',
          room_id: 'r-2',
          room: '成绩同步',
          placed_at: '2026-10-12T09:57:00Z',
          waiting_since: '2026-10-12T09:57:00Z',
          active_at: '2026-10-12T09:57:00Z',
          stopped_at: null,
          idle_deadline: null,
          usage: null,
        },
      ],
    },
    {
      id: 'h-b',
      hostname: 'cheese-pool-91c0',
      machine_id: 415,
      whole_machine: false,
      project: null,
      phase: 'offline',
      status: 'running',
      online: false,
      ip: '10.0.3.24',
      cores: 4,
      memory_mb: 16384,
      disk_gb: 40,
      slots: 5,
      used: 0,
      created_at: '2026-10-12T08:30:00Z',
      enrolled_at: '2026-10-12T08:33:00Z',
      last_seen_at: '2026-10-12T09:58:00Z',
      idle_since: '2026-10-12T09:20:00Z',
      offline_since: '2026-10-12T09:54:00Z',
      failed_at: null,
      released_at: null,
      image_version: '9f2c41d7a3b8e610',
      image_outdated: false,
      draining: false,
      draining_since: null,
      enroll_attempts: 1,
      enroll_error: null,
      usage: {
        read_at: '2026-10-12T09:53:40Z',
        disk_bytes: 40e9,
        disk_used_bytes: 9.1e9,
        inodes: 2_621_440,
        inodes_used: 180_000,
        memory_bytes: 16 * 2 ** 30,
        memory_used_bytes: 1.2 * 2 ** 30,
        swap_bytes: 8 * 2 ** 30,
        swap_used_bytes: 0,
        memory_pressure_some_avg10: 0,
        memory_pressure_some_avg60: 0,
        memory_pressure_full_avg10: 0,
        memory_pressure_full_avg60: 0,
        zswap: false,
        cpu_busy: 0.02,
        load1: 0.1,
        sandboxes: 0,
        memory_history: [0.08, 0.08, 0.07],
        cpu_history: [0.01, 0.02, 0.02],
      },
      sandboxes: [],
    },
    {
      id: 'h-c',
      hostname: 'cheese-pool-02de',
      machine_id: 409,
      whole_machine: false,
      project: null,
      phase: 'failed',
      status: 'error',
      online: false,
      ip: null,
      cores: 4,
      memory_mb: 16384,
      disk_gb: 40,
      slots: 5,
      used: 0,
      created_at: '2026-10-12T09:31:00Z',
      enrolled_at: null,
      last_seen_at: '2026-10-12T09:40:00Z',
      idle_since: null,
      offline_since: null,
      failed_at: '2026-10-12T09:40:00Z',
      released_at: '2026-10-12T09:40:00Z',
      image_version: null,
      image_outdated: null,
      draining: false,
      draining_since: null,
      enroll_attempts: 0,
      enroll_error: null,
      usage: null,
      sandboxes: [],
    },
  ],
  warm: [
    {
      id: 'w-1',
      state: 'ready',
      machine_id: 418,
      hostname: 'warm-7c41e09a2b5d4f18',
      cores: 4,
      memory_mb: 16384,
      disk_gb: 40,
      ip: '10.0.3.30',
      online: true,
      created_at: '2026-10-12T09:45:00Z',
      enrolled_at: '2026-10-12T09:49:00Z',
      attempts: 1,
      error: null,
    },
  ],
  history: {
    since: '2026-10-11T10:00:00Z',
    prepared: 14,
    ready: 13,
    abandoned: 1,
    replaced: 1,
    released_idle: 11,
    released_unpaid: 0,
    wait_p50_s: 48,
    wait_p95_s: 210,
    wait_max_s: 260,
    refused_sessions: 2,
    refused_by_reason: { provider_failing: 2 },
    hosts_lost: 1,
    hosts_lost_by_reason: { provider_error: 1 },
    sandboxes_lost: 0,
  },
}

/** Two days with one empty merge day, hourly samples and both runner kinds. */
const CI_EMPTY = { n: 0, mean: null, p50: null, p90: null, max: null }
export const CRV_CI_BOARD: CiBoard = {
  repo: 'SageSeekerSociety/cheese',
  days: 2,
  since: '2026-10-16T00:00:00+00:00',
  until: '2026-10-18T00:00:00+00:00',
  generated_at: '2026-10-17T22:40:00+00:00',
  sweep: {
    at: '2026-10-17T22:35:00+00:00',
    error: '',
    jobs_owed: 0,
    covered_from: '2026-09-17T22:35:00+00:00',
    complete: true,
  },
  headline: {
    merge_queue_minutes: { n: 140, mean: 92.4, p50: 63.2, p90: 233.1, max: 610 },
    runner_wait_seconds: {
      all: { n: 13000, mean: 230, p50: 58, p90: 760, max: 3300 },
      hosted: { n: 12600, mean: 236, p50: 61, p90: 772, max: 3300 },
      self_hosted: { n: 400, mean: 15, p50: 4, p90: 5, max: 1309 },
    },
    ci_required_minutes: {
      pull_request: { n: 300, mean: 18.3, p50: 12.5, p90: 38.1, max: 106 },
      merge_group: { n: 160, mean: 17.3, p50: 12.1, p90: 37.5, max: 85 },
    },
    rounds: {
      covered_from: '2026-09-18T00:00:00+00:00',
      incomplete_prs: 0,
      prs: 140,
      pr_rounds: { n: 140, mean: 2.1, p50: 1, p90: 4, max: 15 },
      queue_rounds: { n: 140, mean: 1.1, p50: 1, p90: 1, max: 6 },
      more_than_one: 0.5,
      histogram: [
        { rounds: 0, prs: 2 },
        { rounds: 1, prs: 68 },
        { rounds: 2, prs: 36 },
        { rounds: 3, prs: 18 },
        { rounds: 4, prs: 9 },
        { rounds: 5, prs: 7 },
      ],
    },
    fail_then_pass: { runs: 9, of: 610, share: 0.0148 },
    busy_runners: {
      n: 2760,
      mean: 38.9,
      p50: 40,
      p90: 44,
      max: 58,
      share: { pr: 0.63, queue: 0.34, main: 0.02, other: 0.01 },
    },
  },
  daily: [
    {
      day: '2026-10-16',
      merged: 0,
      merge_queue_p50: null,
      merge_queue_p90: null,
      runner_wait_hosted_p50: 48,
      runner_wait_self_hosted_p50: 4,
      ci_required_pr_p50: 12.1,
      ci_required_queue_p50: null,
    },
    {
      day: '2026-10-17',
      merged: 140,
      merge_queue_p50: 63.2,
      merge_queue_p90: 233.1,
      runner_wait_hosted_p50: 61,
      runner_wait_self_hosted_p50: 4,
      ci_required_pr_p50: 12.5,
      ci_required_queue_p50: 12.1,
    },
  ],
  hourly: [6, 7, 8, 9].map((h) => ({
    hour: `2026-10-17T0${h}:00:00+00:00`,
    busy_mean: 30 + h,
    busy_peak: 44 + h,
    pr_mean: 20 + h,
    queue_mean: 10,
    main_mean: 1,
    other_mean: 0,
    hosted_mean: 28 + h,
    self_hosted_mean: 2,
    queue_depth_peak: 8 + h,
  })),
  hour_of_day: Array.from({ length: 24 }, (_, hour) => ({
    hour,
    busy_mean: hour >= 6 && hour <= 9 ? 30 + hour : null,
    busy_peak: hour >= 6 && hour <= 9 ? 44 + hour : null,
    pr_mean: null,
    queue_mean: null,
    main_mean: null,
    other_mean: null,
  })),
  runners: [
    {
      runner: 'hosted: ubuntu-latest',
      kind: 'hosted',
      jobs: 9000,
      failed: 120,
      busy_hours: 610.4,
      utilisation: null,
      wait_p50_s: 61,
      duration_p50_s: 95,
    },
    {
      runner: 'cheese-ci-runner-1',
      kind: 'self_hosted',
      jobs: 140,
      failed: 2,
      busy_hours: 9.6,
      utilisation: 0.2,
      wait_p50_s: 4,
      duration_p50_s: 240,
    },
  ],
  ejections: [
    { reason: 'failed_checks', actor: 'github-merge-queue', count: 59 },
    { reason: 'merge_conflict', actor: 'github-merge-queue', count: 57 },
  ],
}

export const CRV_CI_JOBS: CiJobRow[] = [
  {
    workflow_path: '.github/workflows/required-ci.yml',
    name: 'e2e / e2e',
    workflow: 'Required CI',
    jobs: 160,
    failed: 6,
    duration_s: { n: 160, mean: 640, p50: 610, p90: 900, max: 1300 },
    wait_p50_s: 58,
    busy_hours: 28.4,
  },
  {
    name: 'backend / pure (1)',
    workflow_path: '.github/workflows/required-ci.yml',
    workflow: 'Required CI',
    jobs: 460,
    failed: 3,
    duration_s: { n: 460, mean: 300, p50: 290, p90: 410, max: 700 },
    wait_p50_s: 62,
    busy_hours: 38.3,
  },
]

export const CRV_CI_STEPS: CiStepRow[] = [
  { name: 'Set up job', number: 1, duration_s: { n: 160, mean: 2, p50: 2, p90: 3, max: 9 } },
  { name: 'Run e2e', number: 6, duration_s: { n: 160, mean: 560, p50: 540, p90: 820, max: 1200 } },
  { name: 'Upload report', number: 7, duration_s: { ...CI_EMPTY } },
]
