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
const getAnalyticsParticipants = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getAnalyticsOverview: (...a: unknown[]) => getAnalyticsOverview(...a),
    getAnalyticsAlerts: (...a: unknown[]) => getAnalyticsAlerts(...a),
    getAnalyticsTasks: (...a: unknown[]) => getAnalyticsTasks(...a),
    getAnalyticsPublishers: (...a: unknown[]) => getAnalyticsPublishers(...a),
    getAnalyticsParticipants: (...a: unknown[]) => getAnalyticsParticipants(...a),
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
        submittedParticipantCount: 2,
        successfulParticipantCount: 1,
        // 接口给的是四位小数的比，页面读一位 —— 这一支钉住「读一位」这件事。
        avgParticipantsPerTask: 1.3333,
        submissionConversionRate: 0.67,
        successRate: 0.5,
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

/** 参与者那一份**只有聚合**：没有成员名单，逐人的行无从谈起。 */
const participantsResponse = () => ({
  data: {
    summary: { spaceId: SPACE_ID, from: utcToday() - 180 * DAY, to: utcToday() },
    entityMetrics: {
      participantCount: 5,
      approvedParticipantCount: 4,
      pendingParticipantCount: 1,
      disapprovedParticipantCount: 0,
      submittedParticipantCount: 3,
      successfulParticipantCount: 2,
    },
    studentMetrics: { studentCount: 0, studentsWithRealNameCount: 0 },
    distributions: {},
    trends: { participantsJoined: [], submissionsCreated: [], successesAchieved: [] },
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

/** 切到某个 tab。标签在 `[role="tab"]` 上按文字找 —— 页面里的 tab 不止一个时也能点名。 */
async function openTab(label: string) {
  const el = Array.from(document.querySelectorAll('[role="tab"]')).find((t) => t.textContent?.includes(label)) as
    | HTMLElement
    | undefined
  if (!el) throw new Error(`找不到 tab：${label}`)
  await fireEvent.click(el)
  await waitFor(() => expect(el.getAttribute('aria-selected')).toBe('true'))
}

/** 一张表：表头那一行 + 逐个单元格压平后的正文。 */
function table(title: string) {
  const root = panel(title)
  return {
    head: Array.from(root?.querySelectorAll('thead th') ?? []).map((th) => th.textContent?.trim() ?? ''),
    rows: Array.from(root?.querySelectorAll('tbody tr') ?? []).map((tr) =>
      Array.from(tr.querySelectorAll('td')).map((td) => squash(td.textContent))
    ),
  }
}

/** 日期列写的是 UTC 那一天 —— 与页面那把尺（后端分桶的尺）同一把。 */
function utcDay(ms: number) {
  const d = new Date(ms)
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`
}

describe('整板看板', () => {
  beforeEach(() => {
    getAnalyticsOverview.mockImplementation(async () => overviewResponse())
    getAnalyticsAlerts.mockImplementation(async () => alertsResponse())
    getAnalyticsTasks.mockImplementation(async () => ({ data: { tasks: taskRows() } }))
    getAnalyticsPublishers.mockImplementation(async () => publishersResponse())
    getAnalyticsParticipants.mockImplementation(async () => participantsResponse())
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

  // --- 三个新 tab 与「领了但没动的」那一格 ---------------------------------------

  it('「领了但没动的」那一格在「待处理」里：数是 alerts 的题数，口径写两周，逐人名单如实说不返回', async () => {
    await mount()

    // 原型把它摆在待处理那一格（那一排 alert 之后），**不在总览** —— 摆错了这一条就红。
    expect(panel('领了但没动的')).toBeUndefined()
    await openTab('待处理')

    const p = panel('领了但没动的')
    expect(p).toBeTruthy()
    expect(squash(p?.querySelector('.panel__sub')?.textContent)).toBe('1道题上有已通过的领取者、两周没提交')
    // 数就是 alerts 的 stalledTaskCount，不是另算的。
    expect(squash(p?.querySelector('.stalled')?.textContent)).toBe('1道题')

    const text = squash(p?.textContent)
    expect(text).toContain('两周')
    expect(text).toContain('14天以前')
    expect(text).toContain('逐人的名单接口不返回')

    // 原型那一格列的是「某人在某道题上没动」—— 接口不给这一层，就不列名字、不画行。
    expect(p?.querySelectorAll('.mini li')).toHaveLength(0)
    expect(p?.querySelectorAll('tbody tr')).toHaveLength(0)
  })

  it('「题目」那一格：逐题一行，领取 / 提交 / 通过率 / 状态都来自接口那一份', async () => {
    // 头一题改成「有人交、过了一半」：默认那一份全是 0，读没读那两列看不出来。
    const rows = taskRows()
    rows[0] = { ...rows[0], submittedParticipantCount: 2, successfulParticipantCount: 1, successRate: 0.5 }
    getAnalyticsTasks.mockImplementation(async () => ({ data: { tasks: rows } }))

    await mount()
    await openTab('题目')

    expect(squash(panel('全部题目')?.querySelector('.panel__sub')?.textContent)).toBe('4道·按领取人数排')

    const t = table('全部题目')
    expect(t.head).toEqual(['题目', '出题人', '领取', '提交', '通过率', '状态', '截止'])
    expect(t.rows).toEqual([
      ['最热的一道题基础题', '蔡松洋', '3', '2', '50%', '已上板', utcDay(Date.now() + 2 * DAY)],
      ['没人领的一道题基础题', '蔡松洋', '0', '0', '0%', '已上板', '不限'],
      ['也没人领的一道题基础题', '蔡松洋', '0', '0', '0%', '已上板', '不限'],
      ['还在等审的一道题基础题', '蔡松洋', '0', '0', '0%', '待审核', '不限'],
    ])

    // 题目名链到新外壳的题目详情，不是老树那条地址。
    expect(panel('全部题目')?.querySelector('tbody a')?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/board/tasks/1`)
  })

  it('题一多就走板里那根页码条：一页 20 条，翻页只切一刀', async () => {
    const base = taskRows()[1]
    const many = Array.from({ length: 25 }, (_, i) => ({
      ...base,
      taskId: 100 + i,
      taskName: `第 ${i + 1} 道题`,
    }))
    getAnalyticsTasks.mockImplementation(async () => ({ data: { tasks: many } }))

    await mount()
    await openTab('题目')

    expect(squash(panel('全部题目')?.querySelector('.pb__count')?.textContent)).toBe('共25道·第1/2页')
    expect(panel('全部题目')?.querySelectorAll('tbody tr')).toHaveLength(20)
    expect(table('全部题目').rows[0][0]).toBe('第1道题基础题')

    const next = Array.from(panel('全部题目')?.querySelectorAll('button') ?? []).find((b) =>
      b.textContent?.includes('下一页')
    )
    await fireEvent.click(next!)

    await waitFor(() => expect(panel('全部题目')?.querySelectorAll('tbody tr')).toHaveLength(5))
    expect(table('全部题目').rows[0][0]).toBe('第21道题基础题')
  })

  it('「参与者」那一格只画接口真有的聚合；逐人的那几列如实说不返回', async () => {
    await mount()
    await openTab('参与者')

    const p = panel('参与者')
    expect(squash(p?.querySelector('.panel__sub')?.textContent)).toBe('5个主体领过题·逐人的那一层接口不返回，排不了名')

    // 这六个数来自 /analytics/participants 的 entityMetrics —— overview 那三张卡
    // 说的是「领取 / 提交 / 通过」，报名本身待审多少、被驳回多少它不说。
    expect(kpis()).toEqual({
      报名主体: '5',
      报名已通过: '4',
      报名待审: '1',
      报名已驳回: '0',
      提交主体: '3',
      成功主体: '2',
    })
    expect(getAnalyticsParticipants).toHaveBeenCalledWith(SPACE_ID)

    // 原型那张「每人一行 + 活跃 chip」的表**没有**：接口不返回名单，就不画那一层。
    expect(p?.querySelectorAll('tbody tr')).toHaveLength(0)
    const text = squash(p?.textContent)
    expect(text).toContain('不返回成员名单')
    expect(text).toContain('活跃')
  })

  it('「出题人」那一格：题目数 / 累计被领取 / 平均每道被领 / 出题通过率都来自接口', async () => {
    await mount()
    await openTab('出题人')

    const p = panel('出题人')
    expect(squash(p?.querySelector('.panel__sub')?.textContent)).toBe('开放发题之后，这一格是看「谁在认真出题」的地方')

    const t = table('出题人')
    expect(t.head).toEqual(['出题人', '题目数', '累计被领取', '平均每道被领', '出题通过率'])
    // 平均每道被领是接口给的四位小数比读一位（1.3333 → 1.3）；出题通过率 = successRate。
    expect(t.rows).toEqual([
      ['蔡松洋', '3', '3', '1.3', '50%'],
      ['林小满', '1', '0', '0.0', '0%'],
    ])
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
