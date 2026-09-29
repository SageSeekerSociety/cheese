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
const getAnalyticsPeople = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getAnalyticsOverview: (...a: unknown[]) => getAnalyticsOverview(...a),
    getAnalyticsAlerts: (...a: unknown[]) => getAnalyticsAlerts(...a),
    getAnalyticsTasks: (...a: unknown[]) => getAnalyticsTasks(...a),
    getAnalyticsPublishers: (...a: unknown[]) => getAnalyticsPublishers(...a),
    getAnalyticsParticipants: (...a: unknown[]) => getAnalyticsParticipants(...a),
    getAnalyticsPeople: (...a: unknown[]) => getAnalyticsPeople(...a),
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
    // 没设名额上限的题接口给的是 null（不是 0）—— 表里那一列只写一个数。
    participantLimit: null as number | null,
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

/**
 * 逐人那一格。默认识两个个人 + 一支小队：小队也占一行（`isTeam`），「在做的」是
 * inProgress + submitted —— 交过没判的也算还在做。
 */
const peopleResponse = () => ({
  data: {
    people: [
      { userId: 11, name: '顾云舟', isTeam: false, claims: 3, passed: 1, inProgress: 1, submitted: 1 },
      { userId: 12, name: '陈砚秋', isTeam: false, claims: 1, passed: 1, inProgress: 0, submitted: 0 },
      { userId: 31, name: '第七小队', isTeam: true, claims: 2, passed: 0, inProgress: 2, submitted: 0 },
    ],
    // 默认这一份没有陈账；要验那一格时再单独换一份带 stalled 的。
    stalled: [],
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

/** 一张 KPI 卡（按标签点名）—— 有些断言读的是副行，不是那个数。 */
function kpiCard(label: string): Element | undefined {
  return Array.from(document.querySelectorAll('.metric')).find(
    (card) => card.querySelector('.metric__label')?.textContent?.trim() === label
  )
}

/** 走势卡上「累计 / 每天」那颗切换按钮。 */
function dayToggle(): HTMLButtonElement {
  const el = Array.from(panel('领取与提交')?.querySelectorAll('button') ?? []).find(
    (b) => b.textContent?.trim() === '每天'
  )
  if (!el) throw new Error('找不到「每天」那颗切换按钮')
  return el as HTMLButtonElement
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
    getAnalyticsPeople.mockImplementation(async () => peopleResponse())
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

  it('走势把稀疏的桶铺在它自己的那一天上（累计是默认，「每天」是同一批桶的另一种画法）', async () => {
    await mount()

    // 默认累计：窗口第 1 天那 9 一直累到末尾，所以末值是 9 / 2。
    expect(squash(document.querySelector('.trend__legend')?.textContent)).toContain('累计领取9')
    expect(squash(document.querySelector('.trend__legend')?.textContent)).toContain('累计提交2')
    expect(squash(panel('领取与提交')?.querySelector('.panel__sub')?.textContent)).toBe('最近12天·累计')

    // 切到「每天」：领取那一次在窗口的第 1 天（11 天前），提交那一次在今天。
    // 「最后一天」的读数于是必须是 领取 0 / 提交 2：
    // - 把序列按末尾对齐（把 9 挤到最后一格）→ 领取 9，红；
    // - 把第 i 个点放到第 i 天（当它是稠密的）→ 提交 0，红。
    await fireEvent.click(dayToggle())
    await waitFor(() => expect(squash(document.querySelector('.trend__legend')?.textContent)).toContain('每天领取0'))
    const legend = squash(document.querySelector('.trend__legend')?.textContent)
    expect(legend).toContain('每天领取0')
    expect(legend).toContain('每天提交2')
    expect(squash(panel('领取与提交')?.querySelector('.panel__sub')?.textContent)).toBe('最近12天·每天新增')

    // 有动静就不该给空态：这条与上面那条互为反面，少一边这张图都可能「看着有、其实空」。
    expect(document.body.textContent).not.toContain('最近 12 天没人领取或提交')
  })

  it('KPI 的「领取主体」带近 7 天 / 前 7 天的新增对比，比的是走势按天的桶', async () => {
    const overview = overviewResponse()
    overview.data.trends.participantsJoined = [
      { bucket: utcToday() - 2 * DAY, count: 4 },
      { bucket: utcToday() - 9 * DAY, count: 6 },
    ]
    getAnalyticsOverview.mockImplementation(async () => overview)

    await mount()

    // 近 7 天里只有那 4；9 天前那次落在前 7 天里。文案如实写「近 / 前」，不假装是「本周」。
    const card = kpiCard('领取主体')
    expect(squash(card?.querySelector('.metric__hint')?.textContent)).toBe('近7天新增4·前7天6')
    // 这一周比上一周少，语气是提醒而不是夸奖。
    expect(card?.querySelector('.metric__hint--warn')).toBeTruthy()
    expect(squash(kpiCard('提交主体')?.querySelector('.metric__hint')?.textContent)).toBe('提交率75%')
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

  it('「领了但没动的」那一格在「待处理」里：逐条领取（谁 / 哪道题 / 领了多久），口径两周', async () => {
    getAnalyticsPeople.mockImplementation(async () => ({
      data: {
        people: peopleResponse().data.people,
        stalled: [
          {
            taskId: 1,
            taskTitle: '最热的一道题',
            userId: 11,
            name: '顾云舟',
            isTeam: false,
            claimedAt: Date.now() - 20 * DAY,
          },
          {
            taskId: 7,
            taskTitle: '第九章习题',
            userId: 31,
            name: '第七小队',
            isTeam: true,
            claimedAt: Date.now() - 15 * DAY,
          },
        ],
      },
    }))

    await mount()

    // 原型把它摆在待处理那一格（那一排 alert 之后），**不在总览** —— 摆错了这一条就红。
    expect(panel('领了但没动的')).toBeUndefined()
    await openTab('待处理')

    const p = panel('领了但没动的')
    expect(p).toBeTruthy()
    expect(squash(p?.querySelector('.panel__sub')?.textContent)).toBe('2条领取两周没动·按领取时间排')

    // 一行一条领取：人、题、领了多久（接口按领取时间升序给，最久的在最前）。
    const rows = Array.from(p?.querySelectorAll('.mini li') ?? []).map((li) => squash(li.textContent))
    expect(rows).toEqual(['顾云舟最热的一道题20天前领的', '第七小队第九章习题15天前领的'])
    expect(squash(p?.textContent)).toContain('14天')

    // 题名链到新外壳的题目详情，不是老树那条地址。
    expect(p?.querySelector('.mini__title')?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/board/tasks/1`)
  })

  it('「领了但没动的」在逐人名单读不到时只说明一句，不编名字', async () => {
    getAnalyticsPeople.mockImplementation(async () => {
      throw new Error('403')
    })

    await mount()
    await openTab('待处理')

    const p = panel('领了但没动的')
    expect(squash(p?.querySelector('.panel__sub')?.textContent)).toBe('逐人的名单没读出来')
    expect(p?.querySelectorAll('.mini li')).toHaveLength(0)
    const text = squash(p?.textContent)
    expect(text).toContain('逐人的名单没读出来')
    expect(text).toContain('只对这块板的所有者和管理员开放')

    // 只有这一格塌了：那四张 alert 卡的数来自别的接口，还在。
    expect(squash(document.body.textContent)).toContain('1道题在等你审')
  })

  it('「上板后没人领的」每行带截止日', async () => {
    const rows = taskRows()
    rows[1] = { ...rows[1], deadline: utcToday() + 4 * DAY }
    getAnalyticsTasks.mockImplementation(async () => ({ data: { tasks: rows } }))

    await mount()
    await openTab('待处理')

    const listed = Array.from(panel('上板后没人领的')?.querySelectorAll('.mini li') ?? []).map((li) =>
      squash(li.textContent)
    )
    // 截止日在响应里就有（`deadline`），零领取的题回看时先看还有多久；没设的写「不限」。
    expect(listed).toEqual([
      `没人领的一道题蔡松洋出题·${utcDay(utcToday() + 4 * DAY)}`,
      '也没人领的一道题蔡松洋出题·不限',
    ])
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

  it('领取那一列带名额上限；没设上限的题只有一个数', async () => {
    const rows = taskRows()
    // 头一题设了 5 个名额，其余的接口给 null。
    rows[0] = { ...rows[0], participantLimit: 5 }
    getAnalyticsTasks.mockImplementation(async () => ({ data: { tasks: rows } }))

    await mount()
    await openTab('题目')

    expect(table('全部题目').rows[0][2]).toBe('3/5')
    expect(table('全部题目').rows[1][2]).toBe('0')
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

  it('「参与者」那一格：逐人一行（领取 / 通过 / 在做的 / 状态），外加报名与完成的聚合', async () => {
    await mount()
    await openTab('参与者')

    const p = panel('参与者')
    expect(squash(p?.querySelector('.panel__sub')?.textContent)).toBe('3个主体领过题·按领取数排')

    const t = table('参与者')
    expect(t.head).toEqual(['成员', '领取', '通过', '在做的', '状态'])
    // 「在做的」= 还在做 + 交了没判（原型那一列的口径）；只剩判完的题时 chip 写「都收尾了」。
    // 小队也占一行，成员那一格底下标出来。
    expect(t.rows).toEqual([
      ['顾云舟', '3', '1', '2', '2道在做'],
      ['陈砚秋', '1', '1', '0', '都收尾了'],
      ['第七小队小队', '2', '0', '2', '2道在做'],
    ])
    expect(getAnalyticsPeople).toHaveBeenCalledWith(SPACE_ID)

    // 报名与完成那六个数还在 —— 它来自 /analytics/participants，说的是另一件事
    //（报名审批，不是领取后的状态），所以两张表并排画。
    expect(kpis()).toEqual({
      报名主体: '5',
      报名已通过: '4',
      报名待审: '1',
      报名已驳回: '0',
      提交主体: '3',
      成功主体: '2',
    })
    expect(getAnalyticsParticipants).toHaveBeenCalledWith(SPACE_ID)
    expect(squash(p?.textContent)).toContain('报名与完成（只按主体汇总）')
  })

  it('「参与者」那一格读不到逐人名单时只说明一句，聚合照旧', async () => {
    getAnalyticsPeople.mockImplementation(async () => {
      throw new Error('403')
    })

    await mount()
    await openTab('参与者')

    const p = panel('参与者')
    expect(squash(p?.querySelector('.panel__sub')?.textContent)).toBe('逐人的那一层没读出来')
    expect(p?.querySelectorAll('tbody tr')).toHaveLength(0)
    expect(squash(p?.textContent)).toContain('不照原型编一张人表')
    // 这一格塌了不该带走别的：报名与完成那六个数还在。
    expect(kpis()).toEqual({
      报名主体: '5',
      报名已通过: '4',
      报名待审: '1',
      报名已驳回: '0',
      提交主体: '3',
      成功主体: '2',
    })
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
