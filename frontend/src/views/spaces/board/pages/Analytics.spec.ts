// 整板看板这一页**看到的东西**是不是接口给的那一份。
//
// 断言全部落在屏幕上（KPI 卡上的数字、排行条上的值、那四格待处理的标题、页脚那条
// 链的落点），而不是「源码里调了哪个方法」—— 这一页唯一会悄悄出错的地方就是
// **数字对不上**：取错了字段、把稀疏的走势按下标排、把两张卡的数字弄反。
//
// 接口在 `@/network/api/spaces` 那一层换掉了：真 axios 会被 `src/test/setup-network.ts`
// 逮住（未预期的 fetch 直接判失败），而这一份要测的是「拿到接口的返回之后画成什么」。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getAnalyticsOverview = vi.fn()
const getAnalyticsAlerts = vi.fn()
const getAnalyticsTasks = vi.fn()
const getAnalyticsPublishers = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getAnalyticsOverview: (...a: unknown[]) => getAnalyticsOverview(...a),
    getAnalyticsAlerts: (...a: unknown[]) => getAnalyticsAlerts(...a),
    getAnalyticsTasks: (...a: unknown[]) => getAnalyticsTasks(...a),
    getAnalyticsPublishers: (...a: unknown[]) => getAnalyticsPublishers(...a),
  },
}))

import Analytics from './Analytics.vue'

const SPACE_ID = 7
const DAY = 86_400_000

/** 「今天」的 UTC 零点 —— 后端分桶用的就是它，页面也按它对齐。 */
function utcToday() {
  const d = new Date()
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
}

/** 逐题那一份。t1 有人领没人交、两天后截止；t2/t3 上板后零领取；t4 还在等审。 */
function taskRows() {
  const base = {
    publisher: { id: 1, name: '蔡松洋' },
    category: { id: 3, name: '基础题' },
    createdAt: utcToday() - 30 * DAY,
    pendingParticipantApprovalCount: 0,
    approvedParticipantCount: 0,
    rejectedParticipantCount: 0,
    pendingReviewCount: 0,
    resubmittableCount: 0,
    successfulParticipantCount: 0,
    failedParticipantCount: 0,
    submissionConversionRate: 0,
    successRate: 0,
  }
  return [
    {
      ...base,
      taskId: 1,
      taskName: '最热的一道题',
      approved: 'APPROVED',
      participantCount: 3,
      submittedParticipantCount: 0,
      deadline: Date.now() + 2 * DAY,
    },
    {
      ...base,
      taskId: 2,
      taskName: '没人领的一道题',
      approved: 'APPROVED',
      participantCount: 0,
      submittedParticipantCount: 0,
    },
    {
      ...base,
      taskId: 3,
      taskName: '也没人领的一道题',
      approved: 'APPROVED',
      participantCount: 0,
      submittedParticipantCount: 0,
    },
    {
      ...base,
      taskId: 4,
      taskName: '还在等审的一道题',
      approved: 'NONE',
      participantCount: 0,
      submittedParticipantCount: 0,
    },
  ]
}

function overviewResponse() {
  return {
    data: {
      summary: { spaceId: SPACE_ID, from: utcToday() - 180 * DAY, to: utcToday() },
      entityMetrics: {
        taskCount: 4,
        publisherCount: 2,
        participantCount: 5,
        approvedParticipantCount: 4,
        submittedParticipantCount: 3,
        successfulParticipantCount: 2,
        participationConversionRate: 0.8,
        submissionConversionRate: 0.75,
        successRate: 0.4,
      },
      studentMetrics: { studentCount: 0, approvedStudentCount: 0, successfulStudentCount: 0 },
      taskDistributions: {
        byCategory: {
          name: 'Task Categories',
          type: 'DISCRETE',
          items: [
            { label: '基础题', count: 3 },
            { label: '进阶题', count: 1 },
          ],
        },
        byApprovalStatus: {
          name: 'Task Approval Status',
          type: 'DISCRETE',
          items: [
            { label: 'APPROVED', count: 3 },
            { label: 'NONE', count: 1 },
          ],
        },
        byCompletionStatus: { name: 'Participant Completion Status', type: 'DISCRETE', items: [] },
      },
      // **稀疏**的：只给有动静的那天。领取那一次落在 11 天前（也就是窗口的第 1 天），
      // 提交那一次落在今天 —— 这两个桶是隔着 10 天的，按下标排、按末尾对齐都会露馅。
      trends: {
        tasksCreated: [],
        participantsJoined: [{ bucket: utcToday() - 11 * DAY, count: 9 }],
        submissionsCreated: [{ bucket: utcToday(), count: 2 }],
        successesAchieved: [],
      },
    },
  }
}

const alertsResponse = () => ({
  data: {
    pendingTaskApprovalCount: 1,
    pendingParticipantApprovalCount: 0,
    pendingSubmissionReviewCount: 0,
    stalledTaskCount: 1,
    overdueUnreviewedSubmissionCount: 0,
    inactivePublisherCount: 0,
  },
})

const publishersResponse = () => ({
  data: {
    publishers: [
      {
        publisherId: 1,
        publisherName: '蔡松洋',
        taskCount: 3,
        participantCount: 3,
        approvedParticipantCount: 3,
        submittedParticipantCount: 0,
        successfulParticipantCount: 0,
        avgParticipantsPerTask: 1,
        submissionConversionRate: 0,
        successRate: 0,
        lastTaskCreatedAt: utcToday() - 30 * DAY,
      },
      {
        publisherId: 2,
        publisherName: '林小满',
        taskCount: 1,
        participantCount: 0,
        approvedParticipantCount: 0,
        submittedParticipantCount: 0,
        successfulParticipantCount: 0,
        avgParticipantsPerTask: 0,
        submissionConversionRate: 0,
        successRate: 0,
        lastTaskCreatedAt: utcToday() - 20 * DAY,
      },
    ],
  },
})

const stub = { render: () => null }

async function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: stub },
      { path: '/spaces/:spaceId/board', name: 'SpaceBoardHome', component: stub },
      // 页面自己的那条：空间号是**从地址上读的**，路由对不上这页就取不到 id（也就什么都不取）。
      { path: '/spaces/:spaceId/board/analytics', name: 'SpaceBoardAnalytics', component: stub },
      { path: '/spaces/:spaceId/board/review', name: 'SpaceBoardReview', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
      // 老树那九页的入口：页脚那条链落在这里。
      { path: '/spaces/:spaceId/analytics', name: 'SpacesDetailAnalytics', component: stub },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/board/analytics`)
  await router.isReady()
  return router
}

async function mount() {
  const utils = render(Analytics, {
    global: { plugins: [createVuetify({ components, directives }), await makeRouter()] },
  })
  // 首屏那六张 KPI 卡是「接口回来之后」才有的东西 —— 等它出现，后面才有得量。
  await waitFor(() => expect(document.querySelectorAll('.metric').length).toBeGreaterThan(0))
  return utils
}

/** 一张卡上的文字压成一行：模板里的换行与缩进不该影响断言。 */
const squash = (text: string | null | undefined) => (text ?? '').replace(/\s+/g, '')

/** KPI 卡：标签 → 卡上那个数。 */
function kpis(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const card of Array.from(document.querySelectorAll('.metric'))) {
    const label = card.querySelector('.metric__label')?.textContent?.trim() ?? ''
    out[label] = card.querySelector('.metric__value')?.textContent?.trim() ?? ''
  }
  return out
}

/** 按标题找一块面板 —— 排行条有三处，不限定就分不清量的是哪一块。 */
function panel(title: string): Element | undefined {
  return Array.from(document.querySelectorAll('.panel')).find(
    (p) => p.querySelector('h3')?.textContent?.trim() === title
  )
}

function barRows(title: string) {
  const rows = panel(title)?.querySelectorAll('.bars__row') ?? []
  return Array.from(rows).map((r) => ({
    label: r.querySelector('.bars__label')?.textContent?.trim() ?? '',
    value: r.querySelector('.bars__value')?.textContent?.trim() ?? '',
  }))
}

function splitLegend(title: string) {
  const items = panel(title)?.querySelectorAll('.split__legend li') ?? []
  return Array.from(items).map((li) => ({
    name: li.querySelector('.split__name')?.textContent?.trim() ?? '',
    count: li.querySelector('.split__num')?.textContent?.trim() ?? '',
  }))
}

/** 页脚那条链 —— 老树九页在新外壳里唯一的入口。 */
function oldAnalyticsLink(): HTMLAnchorElement | null {
  const foot = document.querySelector('.an__foot')
  return foot?.querySelector('a') ?? null
}

describe('整板看板', () => {
  beforeEach(() => {
    getAnalyticsOverview.mockImplementation(async () => overviewResponse())
    getAnalyticsAlerts.mockImplementation(async () => alertsResponse())
    getAnalyticsTasks.mockImplementation(async () => ({ data: { tasks: taskRows() } }))
    getAnalyticsPublishers.mockImplementation(async () => publishersResponse())
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('六个 KPI 上的数字就是接口给的那六个', async () => {
    await mount()
    expect(kpis()).toEqual({
      题目总数: '4',
      待审核: '1',
      领取主体: '5',
      提交主体: '3',
      通过主体: '2',
      完成率: '40%',
    })
  })

  it('走势把稀疏的桶铺在它自己的那一天上', async () => {
    await mount()

    // 领取那一次在窗口的第 1 天（11 天前），提交那一次在今天。
    // 「最后一天」的读数于是必须是 领取 0 / 提交 2：
    // - 把序列按末尾对齐（把 9 挤到最后一格）→ 领取 9，红；
    // - 把第 i 个点放到第 i 天（当它是稠密的）→ 提交 0，红。
    const legend = squash(document.querySelector('.trend__legend')?.textContent)
    expect(legend).toContain('每天领取0')
    expect(legend).toContain('每天提交2')

    // 有动静就不该给空态：这条与上面那条互为反面，少一边这张图都可能「看着有、其实空」。
    expect(document.body.textContent).not.toContain('最近 12 天没人领取或提交')
  })

  it('题目构成、分类分布、最热的题、出题人排行，四块都来自接口那一份', async () => {
    await mount()

    expect(splitLegend('题目构成')).toEqual([
      { name: '已上板', count: '3' },
      { name: '待审核', count: '1' },
      { name: '已驳回', count: '0' },
    ])

    expect(barRows('分类分布')).toEqual([
      { label: '基础题', value: '3 道' },
      { label: '进阶题', value: '1 道' },
    ])

    // 最热的题：接口按领取数排好，这里照它画。
    expect(barRows('最热的题').slice(0, 2)).toEqual([
      { label: '最热的一道题', value: '3 人' },
      { label: '没人领的一道题', value: '0 人' },
    ])

    // 出题人排行：值是被领取次数，副行是这个人出了几道题。
    expect(barRows('出题最多的')).toEqual([
      { label: '蔡松洋', value: '3 次' },
      { label: '林小满', value: '0 次' },
    ])
    expect(squash(panel('出题最多的')?.querySelector('.bars__foot')?.textContent)).toContain('蔡松洋：3道题')

    // 逐题那一份是**排序参数**换来的（`sortBy` 不传会 400），所以这条也在这一份里钉住。
    expect(getAnalyticsTasks).toHaveBeenCalledWith(
      SPACE_ID,
      expect.objectContaining({ sortBy: 'participantCount', sortOrder: 'desc' })
    )
  })

  it('待处理那一格：四件事的数分别来自 alerts 与逐题那一份', async () => {
    await mount()

    // 头部那枚「几件待处理」= 四格之和（待审 1 + 三天内截止 1 + 两周没动静 1 + 无人领取 2）。
    expect(squash(document.querySelector('.an__head')?.textContent)).toContain('5件待处理')

    const alertsTab = Array.from(document.querySelectorAll('[role="tab"]')).find((t) =>
      t.textContent?.includes('待处理')
    )
    await fireEvent.click(alertsTab!)
    await waitFor(() => expect(document.body.textContent).toContain('道题在等你审'))

    const alerts = squash(document.body.textContent)
    expect(alerts).toContain('1道题在等你审')
    expect(alerts).toContain('1道题三天内截止')
    expect(alerts).toContain('1道题领了没交、两周没动静')
    expect(alerts).toContain('2道题上板后无人领取')

    // 「去审核」那颗走的是新外壳自己的审核页，不是老树那条地址。
    const goReview = Array.from(document.querySelectorAll('a')).find((a) => a.textContent?.includes('去审核'))
    expect(goReview?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/board/review`)

    // 有人领、零提交的那道题列在下面，点它进新外壳的题目详情。
    const listed = Array.from(document.body.querySelectorAll('.mini__title')).map((a) => a.textContent)
    expect(listed).toEqual(['最热的一道题', '没人领的一道题', '也没人领的一道题'])
    const first = document.body.querySelector('.mini__title')
    expect(first?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/board/tasks/1`)
  })

  it('页脚那条链把人送到老树那九页 —— 老地址还在，路没断', async () => {
    await mount()
    expect(oldAnalyticsLink()?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/analytics`)
    expect(squash(oldAnalyticsLink()?.textContent)).toBe('打开老版九页分析')
  })

  it('接口答不上来时说的是「读不到」，不是一屏看着像空板的零', async () => {
    getAnalyticsOverview.mockImplementation(async () => {
      throw new Error('403')
    })
    render(Analytics, {
      global: { plugins: [createVuetify({ components, directives }), await makeRouter()] },
    })
    await waitFor(() => expect(document.body.textContent).toContain('打不开这块看板'))
    // 一句说明，而不是六张写着 0 的卡 —— 后者会被读成「这块板什么都没有」。
    expect(document.querySelectorAll('.metric')).toHaveLength(0)
  })
})
