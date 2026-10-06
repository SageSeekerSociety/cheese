/**
 * 芝士额度的两页在预览站里的条目：个人设置里的「芝士额度」和团队的「额度」。
 * 数据照 `/users/me/credits/usage`、`/teams/{id}/credits/usage` 的回答形状写，额度数以点计。
 */
import type { CreditUsage, UsagePack, UsagePeriod } from '@/lib/creditUsage'
import type { CatalogEntry, CatalogNeed } from './catalog'

import TeamCreditsView from '@/views/teams/detail/CreditsView.vue'
import UsageSettingsView from '@/views/user/settings/UsageView.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n', 'router']

function days(spent: number[]): CreditUsage['days'] {
  return Array.from({ length: 31 }, (_, i) => {
    const credits = i < spent.length ? spent[i] ?? 0 : null
    return {
      date: `2026-10-${String(i + 1).padStart(2, '0')}`,
      credits,
      lines: {
        collab: (credits ?? 0) * 0.5,
        ask: (credits ?? 0) * 0.2,
        write: (credits ?? 0) * 0.1,
        compute: (credits ?? 0) * 0.2,
      },
    }
  })
}

const SPENT = [42, 18, 0, 65, 30, 12, 0, 0, 51, 22, 9, 0, 14, 7]

const MONTH: UsagePeriod = {
  unlimited: false,
  credits_total: 500,
  credits_used: 270,
  used_ratio: 0.54,
  remaining_ratio: 0.46,
  resets_at: '2026-10-31T16:00:00+00:00',
}

const PACKS: UsagePack[] = [
  {
    id: 'g1',
    source: 'admin_grant',
    project_id: null,
    task_id: null,
    project_name: null,
    task_name: null,
    credits_total: 1000,
    credits_remaining: 820,
    remaining_ratio: 0.82,
    expires_at: '2026-12-31T15:59:59+00:00',
  },
  {
    id: 't1',
    source: 'task_earmark',
    project_id: 'p1',
    task_id: 9,
    project_name: '空气质量看板',
    task_name: '城市空气质量数据分析',
    credits_total: 400,
    credits_remaining: 400,
    remaining_ratio: 1,
    expires_at: null,
  },
]

const PERSON: CreditUsage = {
  plan: {
    key: 'free',
    name: 'Free',
    unlimited: false,
    credits_per_period: 500,
    windows: [],
    models: ['DeepSeek Flash', 'GLM 5'],
  },
  period: MONTH,
  windows: [],
  packs: PACKS,
  days: days(SPENT),
  projects: [
    { id: 'p1', name: '空气质量看板', credits: 162 },
    { id: 'p2', name: '课程笔记整理', credits: 40 },
  ],
  lines: { collab: 150, ask: 52, write: 16, compute: 52 },
  teams: [
    {
      id: 3,
      name: '城市数据实验室',
      handle: 'citylab',
      plan: { key: 'free', name: 'Free' },
      unlimited: false,
      remaining_ratio: 0.12,
      credits_remaining: 60,
    },
    {
      id: 4,
      name: '机器人社',
      handle: 'robots',
      plan: { key: 'pro', name: 'Pro' },
      unlimited: false,
      remaining_ratio: 0.6,
      credits_remaining: null,
    },
  ],
}

const WINDOWED: CreditUsage = {
  ...PERSON,
  plan: {
    key: 'pro',
    name: 'Pro',
    unlimited: false,
    credits_per_period: null,
    windows: [
      { hours: 5, calendar: null, credits: 500 },
      { hours: null, calendar: 'week', credits: 5000 },
    ],
    models: ['DeepSeek Flash', 'GLM 5', 'Claude Sonnet 5'],
  },
  period: null,
  windows: [
    { hours: 5, calendar: null, used_ratio: 0.38, resets_at: '2026-10-14T09:20:00+00:00' },
    { hours: null, calendar: 'week', used_ratio: 0.12, resets_at: '2026-10-18T16:00:00+00:00' },
  ],
  packs: [],
  lines: { collab: 150, compute: 52 },
  teams: undefined,
}

const TEAM: CreditUsage = {
  ...PERSON,
  packs: [],
  days: days(SPENT).map((d) => ({
    ...d,
    lines: { collab: (d.credits ?? 0) * 0.7, compute: (d.credits ?? 0) * 0.3 },
  })),
  lines: { collab: 189, compute: 81 },
  teams: undefined,
}

export const USAGE_ENTRIES: CatalogEntry[] = [
  {
    id: 'usage-settings-view',
    title: 'UsageSettingsView',
    about: '个人设置里的「芝士额度」：本月用了多少点、其他额度、每天用量、我的项目、我所在的团队。',
    file: 'src/views/user/settings/UsageView.vue',
    component: UsageSettingsView,
    needs: UI,
    states: [
      {
        name: '本月用了一半',
        note: '额度数以点计；其他额度写明什么时候用到它。方案名点开看方案包含什么。',
        props: { usage: PERSON, loading: false, error: null },
        expect: '空气质量看板',
      },
      {
        name: '方案额度用完，还有其他额度',
        note: '不说「本月已用完」：还在从其他额度里扣。',
        props: {
          usage: { ...PERSON, period: { ...MONTH, credits_used: 500, used_ratio: 1, remaining_ratio: 0 } },
          loading: false,
          error: null,
        },
        expect: '方案额度已用完，正在使用其他额度',
      },
    ],
  },
  {
    id: 'team-credits-view',
    title: 'TeamCreditsView',
    about: '团队的「额度」：方案、本月（按协作、算力分）或各个使用上限、其他额度、每天用量、按项目。',
    file: 'src/views/teams/detail/CreditsView.vue',
    component: TeamCreditsView,
    needs: UI,
    states: [
      {
        name: '按月发放的方案',
        note: '本月的条和每天的柱子按协作和算力分两色；团队没有问答和写作。',
        props: { usage: TEAM, loading: false, error: null },
        expect: '算力',
      },
      {
        name: '按时间窗口限额的方案',
        note: '没有月额度，每个窗口一行，写用了多少和什么时候清零。',
        props: { usage: WINDOWED, loading: false, error: null },
        expect: '5 小时内已用 38%',
      },
    ],
  },
]
