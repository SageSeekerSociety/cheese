/**
 * 方案与额度那五件在预览站里的条目：方案表、团队表、团队面板、发额度框、方案框。
 *
 * 单独一份是因为 `catalog.ts` 已经接近 `frontend/src` 那一千行的上限，和
 * `catalogModels.ts` 同一个理由。数据照 `/admin/plans`、`/admin/teams` 的回答形状写。
 */
import type { CreditAudit, CreditPack, CreditTeamDetail, CreditTeamPage, Plan } from '@/lib/adminCredits'
import type { CatalogEntry, CatalogNeed } from './catalog'

import AdminCreditTeamPanel from '@/components/admin/credits/AdminCreditTeamPanel.vue'
import AdminCreditTeamsTable from '@/components/admin/credits/AdminCreditTeamsTable.vue'
import AdminGrantDialog from '@/components/admin/credits/AdminGrantDialog.vue'
import AdminPlanDialog from '@/components/admin/credits/AdminPlanDialog.vue'
import AdminPlansTable from '@/components/admin/credits/AdminPlansTable.vue'

const UI: CatalogNeed[] = ['vuetify', 'i18n']

const FREE: Plan = {
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

const RESERVE: Plan = {
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

const CLASSROOM: Plan = {
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

const PLANS = [FREE, RESERVE, CLASSROOM]

function pack(over: Partial<CreditPack>): CreditPack {
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

const LAB_PACKS: CreditPack[] = [
  pack({ id: 'p1', credits_used: 115 }),
  pack({
    id: 'p2',
    source: 'admin_grant',
    credits_total: 300,
    credits_used: 90,
    period_start: null,
    expires_at: '2026-12-31T15:59:59+00:00',
    reason: '期中项目展示前临时加量',
  }),
  pack({
    id: 'p3',
    source: 'task_earmark',
    credits_total: 50,
    credits_used: 20,
    period_start: null,
    expires_at: null,
    project_id: 'b6c1',
    task_id: 12,
    project_name: '空气质量看板',
    task_name: '城市空气质量数据分析',
  }),
]

const TEAMS: CreditTeamPage = {
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
      packs: LAB_PACKS,
    },
    {
      id: 3,
      name: '个人',
      handle: 'linzy-personal',
      personal_owner: 'linzy',
      personal_owner_nickname: '林知远',
      member_count: null,
      plan_key: 'free',
      period: { start: '2026-10-01T00:00:00+00:00', credits_total: 125, credits_used: 46 },
      packs: [pack({ id: 'p4', credits_used: 46 })],
    },
    {
      id: 4,
      name: '机器人社',
      handle: 'robotics',
      personal_owner: null,
      personal_owner_nickname: null,
      member_count: 12,
      plan_key: 'free',
      period: { start: '2026-10-01T00:00:00+00:00', credits_total: null, credits_used: 0 },
      packs: [],
    },
  ],
  total: 4,
  page: 1,
  page_size: 20,
}

const LAB: CreditTeamDetail = {
  id: 2,
  name: '城市数据实验室',
  handle: 'citylab',
  personal_owner: null,
  personal_owner_nickname: null,
  member_count: 6,
  plan: FREE,
  period: { start: '2026-10-01T00:00:00+00:00', credits_total: 125, credits_used: 115 },
  packs: LAB_PACKS,
}

const HISTORY: CreditAudit[] = [
  {
    created_at: '2026-10-12T06:05:00+00:00',
    actor_handle: 'starry',
    action: 'team.grant',
    target: '2',
    before: null,
    after: { credits_total: 300, expires_at: '2026-12-31T15:59:59+00:00' },
  },
  {
    created_at: '2026-09-30T01:12:00+00:00',
    actor_handle: 'starry',
    action: 'team.plan',
    target: '2',
    before: { plan_key: 'reserve' },
    after: { plan_key: 'free' },
  },
]

export const CREDITS_ENTRIES: CatalogEntry[] = [
  {
    id: 'admin-plans-table',
    title: 'AdminPlansTable',
    about: '方案一览：每月额度、使用上限、可用模型。',
    file: 'src/components/admin/credits/AdminPlansTable.vue',
    component: AdminPlansTable,
    needs: UI,
    states: [
      {
        name: '三个方案',
        note: '默认方案和仅后台的方案各带一个标签；不限的方案每月额度写「不限」。',
        props: { plans: PLANS, loading: false, error: null },
        expect: 'Classroom',
      },
      {
        name: '读不到',
        note: '读失败不画成「暂无方案」：标题说读不到，原话作说明，重试就在旁边。',
        props: { plans: null, loading: false, error: 'connect ECONNREFUSED' },
        expect: '无法读取方案',
      },
    ],
  },
  {
    id: 'admin-credit-teams-table',
    title: 'AdminCreditTeamsTable',
    about: '团队一览：方案、本月方案额度、可用余额。',
    file: 'src/components/admin/credits/AdminCreditTeamsTable.vue',
    component: AdminCreditTeamsTable,
    needs: UI,
    states: [
      {
        name: '一页团队',
        note: '个人团队写主人的昵称和 handle，团队写成员数；本月方案额度还没发时写「本月尚未发放」，不画成 0。',
        props: { page: TEAMS, plans: PLANS, loading: false, error: null, searching: false },
        expect: '林知远',
      },
      {
        name: '搜不到',
        note: '搜索时的空态说「没有匹配的」，不说「还没有团队」。',
        props: {
          page: { items: [], total: 0, page: 1, page_size: 20 },
          plans: PLANS,
          loading: false,
          error: null,
          searching: true,
        },
        expect: '暂无匹配的团队',
      },
    ],
  },
  {
    id: 'admin-credit-team-panel',
    title: 'AdminCreditTeamPanel',
    about: '一个团队的额度面板：方案、每一笔额度、操作记录。',
    file: 'src/components/admin/credits/AdminCreditTeamPanel.vue',
    component: AdminCreditTeamPanel,
    needs: UI,
    layout: true,
    states: [
      {
        name: '三笔额度',
        note: '方案额度、管理员发放、题目额度各一笔；题目额度写明只能用在哪个项目。',
        props: { modelValue: true, team: LAB, plans: PLANS, history: HISTORY },
        expect: '管理员发放',
      },
      {
        name: '改方案被拒',
        note: '服务端的原话原样显示，下拉框留在原来的方案上。',
        props: {
          modelValue: true,
          team: LAB,
          plans: PLANS,
          history: [],
          planError: '方案「Reserve」只给团队',
        },
        expect: '方案「Reserve」只给团队',
      },
    ],
  },
  {
    id: 'admin-grant-dialog',
    title: 'AdminGrantDialog',
    about: '给一个团队发额度：数量、到期时间、原因。',
    file: 'src/components/admin/credits/AdminGrantDialog.vue',
    component: AdminGrantDialog,
    needs: UI,
    layout: true,
    teleport: true,
    states: [
      {
        name: '打开',
        note: '数量填了才能发放；到期默认不过期，选了日期按那一天的最后一刻算。',
        props: { modelValue: true, teamName: '城市数据实验室' },
        expect: '给「城市数据实验室」发额度',
      },
    ],
  },
  {
    id: 'admin-plan-dialog',
    title: 'AdminPlanDialog',
    about: '新建或编辑一个方案：名称、适用对象、额度与使用上限、可用模型。',
    file: 'src/components/admin/credits/AdminPlanDialog.vue',
    component: AdminPlanDialog,
    needs: UI,
    layout: true,
    teleport: true,
    states: [
      {
        name: '编辑',
        note: '编辑时底部说明改动从下个月起生效。',
        props: {
          modelValue: true,
          plan: CLASSROOM,
          tierModels: {
            included: ['deepseek-flash', 'mimo-v2.6-pro', 'glm-4.7'],
            premium: ['kimi-k3'],
            frontier: [],
          },
        },
        expect: '下个月起生效',
      },
      {
        name: '新建',
        note: '新建时只勾 Standard，每月发放留空等人填。',
        props: { modelValue: true, plan: null },
        expect: '新建方案',
      },
    ],
  },
]
