// 新外壳里的题目详情 —— **这一页自己**画出来的东西是不是接口给的那一份。
//
// 第八批按原型重画之后这一页底下不再套老页面（`wrappers.spec.ts` 那条接缝因此也没了），
// 所以这一份量的是它自己的三件事：
//
// - 屏幕上的数字与状态**只来自接口**（题目、领取人数、截止、形式、提交与通过）；
// - 领取那颗按钮的每一种样子都由接口的事实决定（可领 / 已领 / 待审核 / 已驳回 / 人数已满 / 已截止）；
// - 「少画，不编」的那几处**确实没画**：名单接口没开就不画名单、不数提交、不画走势。
//
// 接口在 `@/network/api/*` 那一层换掉：真 axios 会被 `src/test/setup-network.ts` 逮住
// （未预期的 fetch 直接判失败）。附件清单**不替** —— 下载次数与 `canDownload` 都是
// `TaskAttachmentList` 的事，替掉就等于把「这一页的数字从哪来」这条断言一起替掉了。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const taskDetail = vi.fn()
const listSubmissions = vi.fn()
const listAttachments = vi.fn()
const getParticipants = vi.fn()
const postSubmissionReview = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    detail: (...a: unknown[]) => taskDetail(...a),
    listSubmissions: (...a: unknown[]) => listSubmissions(...a),
    listAttachments: (...a: unknown[]) => listAttachments(...a),
    getParticipants: (...a: unknown[]) => getParticipants(...a),
    postSubmissionReview: (...a: unknown[]) => postSubmissionReview(...a),
  },
}))

const getSubmissionQueue = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { getSubmissionQueue: (...a: unknown[]) => getSubmissionQueue(...a) },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 这一页挂载时会设浏览器标题，那条路要 i18n —— 与 `TopicView.deepLink.spec.ts` 同一个替身。
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 页头那一层：真组件走的是面包屑（只有老树的路由 meta 拼得出来），这里只留它的插槽 ——
// 插槽里那颗「回到题目板」与「出题人·某某」都是真的。
vi.mock('@/components/common/PageHeader.vue', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  return {
    default: dc({
      name: 'PageHeaderStub',
      setup(_, { slots }) {
        return () => hh('div', { 'data-testid': 'page-header' }, slots.default?.())
      },
    }),
  }
})

// 领取/退队那几张对话框与对话入口都挂在老机器上（事件总线 + `TaskDialogs`），
// 这一页只是把线接上 —— 界面上它们是别的东西，这里只留一个空壳，免得测到别人家里去。
vi.mock('@/views/tasks/components', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  return {
    AIChatButton: dc({ name: 'AIChatButtonStub', props: { open: Boolean }, setup: () => () => hh('div') }),
    TaskDialogs: dc({
      name: 'TaskDialogsStub',
      props: [
        'taskData',
        'availableTeams',
        'loadingTeams',
        'joinedTeams',
        'selectedLeaveTeamId',
        'selectedContext',
        'participationInfo',
      ],
      setup: () => () => hh('div'),
    }),
    LoadingErrorContainer: dc({
      name: 'LoadingErrorContainerStub',
      props: ['loading', 'error'],
      setup:
        (_, { slots }) =>
        () =>
          hh('div', { 'data-testid': 'loading-error' }, slots.default?.()),
    }),
  }
})

vi.mock('@/components/tasks/TaskSubmissionHistory.vue', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  return {
    default: dc({
      name: 'TaskSubmissionHistoryStub',
      props: ['taskId', 'participantId', 'reviewable', 'isDialog', 'outlined', 'highlightLatest', 'title'],
      setup: () => () => hh('div', { 'data-testid': 'submission-history' }),
    }),
  }
})

import TaskDetail from './TaskDetail.vue'

import AccountService from '@/services/account'

const SPACE_ID = 7
const TASK_ID = 42
const DAY = 86_400_000

/** 题目那一份：**每一格都是接口给的**，测试再按需要覆盖。 */
const BASE_TASK = {
  id: TASK_ID,
  name: '把红黑树插一遍',
  // 出处那一串是发题那条路（从 PDF 发题）写进 `intro` 的，格式由 `../model.ts` 认。
  intro: '【PDF · 第 2 页】写出每一步旋转',
  description: '**要求**：写出每一步旋转',
  approved: 'APPROVED',
  rejectReason: '',
  category: { id: 3, name: '基础题' },
  creator: { id: 1, username: 'cai', nickname: '蔡松洋' },
  topics: [{ id: 5, name: '树' }],
  videoUrl: '',
  participants: { total: 0, examples: [] },
  participantLimit: 0,
  deadline: null as number | null,
  registrationStartAt: null as number | null,
  submitterType: 'USER',
  minTeamSize: 1,
  maxTeamSize: 1,
  space: { id: SPACE_ID, name: '数据结构空间', admins: [] },
}

function payload(task: Record<string, unknown> = {}, participation: Record<string, unknown> = {}) {
  return {
    data: {
      task: { ...BASE_TASK, ...task },
      participation: { hasParticipation: false, identities: [], ...participation },
    },
  }
}

const stub = { render: () => null }

/** 这一页自己会跳的那几条 + 它底下那四格 —— `to` 是渲染时就解的，缺一条就抛。 */
function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/board', name: 'SpaceBoardHome', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId/submit', name: 'SpaceBoardTaskSubmit', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId/submissions', name: 'SpaceBoardTaskSubmissions', component: stub },
      {
        path: '/spaces/:spaceId/board/tasks/:taskId/participants',
        name: 'SpaceBoardTaskParticipants',
        component: stub,
      },
      { path: '/spaces/:spaceId/board/tasks/:taskId/ai-advice', name: 'SpaceBoardTaskAIAdvice', component: stub },
      // 改题页这一批没搬，还是老树那一页。
      { path: '/spaces/:spaceId/tasks/:taskId/edit', name: 'TasksEdit', component: stub },
    ],
  })
}

/** 挂这一页：题目 id 与空间 id 都**从地址上读**，接口那一份由 `taskDetail` 给。 */
async function mount(task: Record<string, unknown> = {}, participation: Record<string, unknown> = {}) {
  taskDetail.mockImplementation(async () => payload(task, participation))
  const router = makeRouter()
  await router.push(`/spaces/${SPACE_ID}/board/tasks/${TASK_ID}`)
  await router.isReady()
  const utils = render(TaskDetail, {
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  // 首屏是「接口回来之后」才有的东西 —— 等它出现，后面才有得量。
  await waitFor(() => expect(document.querySelector('.td__title')).not.toBeNull())
  return utils
}

/** 模板里的换行与缩进不该影响断言。 */
const squash = (text: string | null | undefined) => (text ?? '').replace(/\s+/g, '')

/** 领取那颗按钮此刻的样子。 */
function claimOf(container: Element) {
  const btn = container.querySelector<HTMLButtonElement>('.td__claim-btn')
  return { label: btn?.textContent?.trim() ?? null, disabled: btn?.disabled ?? null }
}

/** 右栏那四行事实：`dt` → `dd`。 */
function factsOf(container: Element): Record<string, string> {
  const out: Record<string, string> = {}
  for (const row of Array.from(container.querySelectorAll('.td__facts > div'))) {
    out[row.querySelector('dt')?.textContent?.trim() ?? ''] = row.querySelector('dd')?.textContent?.trim() ?? ''
  }
  return out
}

/** 屏幕上那几块面板的标题。有没有画，看的就是这里。 */
function panelTitles(container: Element): string[] {
  return Array.from(container.querySelectorAll('.panel h3')).map((h) => h.textContent?.trim() ?? '')
}

function rosterRows(container: Element): Element[] {
  return Array.from(container.querySelectorAll('.roster li'))
}

function buttonWith(scope: Element, text: string): HTMLButtonElement | null {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.includes(text)) ?? null
}

describe('新外壳里的题目详情', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // 默认是「路人」：这一页对非出题人、非管理员的样子就是下面那几条断言量的东西。
    AccountService.user = null
    listSubmissions.mockImplementation(async () => ({ data: { submissions: [], page: {} } }))
    listAttachments.mockImplementation(async () => ({ data: { attachments: [], canDownload: false } }))
    getParticipants.mockImplementation(async () => ({ data: { participants: [] } }))
    getSubmissionQueue.mockImplementation(async () => ({ data: { submissions: [], summary: {}, page: {} } }))
    postSubmissionReview.mockImplementation(async () => ({ data: {} }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('题目、领取人数、截止、形式都是 GET /tasks/{id} 给的那一份', async () => {
    const { container } = await mount({
      participants: { total: 3, examples: [] },
      participantLimit: 5,
      deadline: Date.now() + 3 * DAY,
    })

    expect(taskDetail).toHaveBeenCalledWith(TASK_ID)
    expect(container.querySelector('.td__title')?.textContent).toContain('把红黑树插一遍')
    expect(squash(container.querySelector('.td__publisher')?.textContent)).toBe('出题人·蔡松洋')
    // 出处与摘要是 `intro` 那一段的两半，`〔…〕` 摘掉之后剩下的才是摘要。
    expect(squash(container.querySelector('.td__origin')?.textContent)).toBe('PDF·第2页')
    expect(squash(container.querySelector('.td__summary')?.textContent)).toBe('写出每一步旋转')

    // 右栏：领取人数就是接口给的 `participants.total`（不是把名单数出来的）。
    expect(squash(container.querySelector('.td__count')?.textContent)).toBe('3/5人')
    const facts = factsOf(container)
    expect(facts['截止']).toBe('3 天后截止')
    expect(facts['形式']).toBe('个人')
  })

  it('材料那一块挂的是真清单：下载次数与能不能下载都由服务端说', async () => {
    listAttachments.mockImplementation(async () => ({
      data: { attachments: [{ id: 9, name: '题面.pdf', size: 2048, downloadCount: 3 }], canDownload: false },
    }))
    const { container } = await mount()

    expect(listAttachments).toHaveBeenCalledWith(TASK_ID)
    await waitFor(() => expect(container.querySelector('[data-testid="task-attachment"]')).not.toBeNull())
    expect(squash(container.textContent)).toContain('已下载3次')
    // `canDownload` 也是服务端说的：它说不能下，这一行就不给下载按钮，也不自己拿角色去猜。
    expect(container.textContent).toContain('领取这道题之后才能下载')
  })

  it('可领：审过了、有人领但没满、没截止 —— 按钮点得动', async () => {
    const { container } = await mount({ participants: { total: 1, examples: [] }, participantLimit: 5 })

    expect(claimOf(container)).toEqual({ label: '领取这道题', disabled: false })
  })

  it('已领取：这道题我已经领了（参与身份 APPROVED），领取之后才多出那几条去路', async () => {
    const { container } = await mount({}, { hasParticipation: true, identities: [{ id: 11, approved: 'APPROVED' }] })

    expect(claimOf(container)).toEqual({ label: '你已经领取', disabled: true })

    // 老页面把这颗「提交」放在一格 Tab 里，这一页按原型排在领取旁边 —— 两条去路落的
    // 都是**新树**的地址，e2e 点的就是它们，所以在这里钉住。
    expect(Array.from(container.querySelectorAll('.td__claim a')).map((a) => a.getAttribute('href'))).toEqual([
      `/spaces/${SPACE_ID}/board/tasks/${TASK_ID}/submit`,
      `/spaces/${SPACE_ID}/board/tasks/${TASK_ID}/submissions`,
    ])
    expect(buttonWith(container, '退出这道题')).not.toBeNull()
  })

  it('待审核：这道题自己还在等审', async () => {
    const { container } = await mount({ approved: 'NONE' })

    expect(claimOf(container)).toEqual({ label: '待审核', disabled: true })
  })

  it('已驳回：驳回原因画的就是接口写的那一段，不补一句套话', async () => {
    const { container } = await mount({ approved: 'DISAPPROVED', rejectReason: '格式不对，请贴原始 PDF' })

    expect(claimOf(container)).toEqual({ label: '已驳回', disabled: true })
    expect(squash(container.querySelector('.td__reject')?.textContent)).toBe('审核未通过：格式不对，请贴原始PDF')
  })

  it('人数已满：上限是接口给的 `participantLimit`，0 是「不限」不是「一个都不许」', async () => {
    const full = await mount({ participants: { total: 3, examples: [] }, participantLimit: 3 })
    expect(claimOf(full.container)).toEqual({ label: '人数已满', disabled: true })
    cleanup()

    // 同一份人数，上限 0（不限）时仍然领得动。
    const unlimited = await mount({ participants: { total: 3, examples: [] }, participantLimit: 0 })
    expect(claimOf(unlimited.container)).toEqual({ label: '领取这道题', disabled: false })
  })

  it('已截止：截止时刻来自接口，过了就是过了', async () => {
    const { container } = await mount({ deadline: Date.now() - DAY })

    expect(claimOf(container)).toEqual({ label: '已截止', disabled: true })
    expect(factsOf(container)['截止']).toBe('已截止 1 天')
  })

  it('已领取时我那一份进度按最新一版提交与它的评审算', async () => {
    listSubmissions.mockImplementation(async () => ({
      data: { submissions: [{ id: 88, version: 3, review: { detail: { accepted: true } } }], page: {} },
    }))
    const { container } = await mount({}, { hasParticipation: true, identities: [{ id: 11, approved: 'APPROVED' }] })

    // 取的是**我**那一份的最新一版，带着评审一起要 —— 判没判是这一行唯一的判据。
    expect(listSubmissions).toHaveBeenCalledWith(TASK_ID, 11, {
      pageSize: 1,
      sort_by: 'createdAt',
      sort_order: 'desc',
      queryReview: true,
    })
    await waitFor(() => expect(container.querySelector('.td__mine')?.textContent).toContain('已通过'))
    expect(squash(container.querySelector('.td__mine')?.textContent)).toContain('第3版')
  })

  it('不是出题人：名单、提交与通过数、走势一概不画 —— 接口没开的就不估', async () => {
    const { container } = await mount({ participants: { total: 3, examples: [] } })

    // 名单那条接口由服务端按 `may_teach_task` 把关，这里连问都不该问。
    expect(getParticipants).not.toHaveBeenCalled()
    expect(panelTitles(container)).not.toContain('领取者')
    expect(panelTitles(container)).not.toContain('领取走势')

    const facts = factsOf(container)
    expect(facts['提交']).toBe('—')
    expect(facts['通过']).toBe('—')
    expect(squash(container.querySelector('.td__side .td__note')?.textContent)).toContain('不填一个估的数')
    expect(squash(container.querySelector('.td__sources')?.textContent)).toContain('403')
  })

  it('出题人：名单逐人一行，交了没判的那一行才有通过/不通过，点了就发真评审', async () => {
    AccountService.user = { id: 1, nickname: '蔡松洋' } as never
    getParticipants.mockImplementation(async () => ({
      data: {
        participants: [
          { id: 11, approved: 'APPROVED', createdAt: Date.now() - 2 * DAY, member: { name: '林小满' } },
          { id: 12, approved: 'APPROVED', createdAt: Date.now() - DAY, member: { name: '周舟' } },
        ],
      },
    }))
    getSubmissionQueue.mockImplementation(async () => ({
      data: {
        submissions: [{ id: 501, participantId: 12, version: 2, review: null }],
        summary: {},
        page: {},
      },
    }))
    const { container } = await mount({ participants: { total: 2, examples: [] } })

    await waitFor(() => expect(rosterRows(container).length).toBe(2))
    expect(getParticipants).toHaveBeenCalledWith(TASK_ID)
    expect(getSubmissionQueue).toHaveBeenCalledWith(SPACE_ID, { taskId: TASK_ID, pageSize: 200 })

    // 按领取时间倒序：刚领的在最上面。
    expect(rosterRows(container).map((r) => r.querySelector('.roster__name')?.textContent?.trim())).toEqual([
      '周舟',
      '林小满',
    ])
    // 两个数是从名单 + 提交队列里数出来的，所以看得到名单时才画。
    const facts = factsOf(container)
    expect(facts['提交']).toBe('1 份')
    expect(facts['通过']).toBe('0 份')
    expect(panelTitles(container)).toContain('领取走势')

    // 交了没判的那一行：两颗按钮都在；没交的那一行一颗都没有。
    const [zhou, lin] = rosterRows(container)
    expect(zhou.textContent).toContain('已提交')
    expect(buttonWith(lin, '通过')).toBeNull()

    await fireEvent.click(buttonWith(zhou, '通过')!)
    // 这一颗只判过没过，所以评分那一格是 0（未评分）—— 判就是真的判，走真接口。
    expect(postSubmissionReview).toHaveBeenCalledWith(TASK_ID, 12, 501, { accepted: true, score: 0, comment: '' })
  })

  it('名单接口 403：写明为什么，不画空表也不画走势', async () => {
    AccountService.user = { id: 1, nickname: '蔡松洋' } as never
    getParticipants.mockImplementation(async () => {
      throw new Error('403')
    })
    const { container } = await mount({ participants: { total: 3, examples: [] } })

    await waitFor(() => expect(container.querySelector('.td__guard')).not.toBeNull())
    expect(squash(container.querySelector('.td__guard')?.textContent)).toContain('不画名单')
    // 拿不到名单时交白卷：既不画一张空表，也不把满格的人数按 0 份交算。
    expect(panelTitles(container)).not.toContain('领取者')
    expect(panelTitles(container)).not.toContain('领取走势')
    expect(factsOf(container)['提交']).toBe('—')
  })

  it('视频：B 站链接嵌播放器', async () => {
    const { container } = await mount({ videoUrl: 'https://www.bilibili.com/video/BV1xx411c7mD' })

    expect(container.querySelector('iframe.td__video')?.getAttribute('src')).toBe(
      '//player.bilibili.com/player.html?bvid=BV1xx411c7mD&autoplay=0'
    )
  })

  it('视频：别的平台只给一条链接，不假装能嵌', async () => {
    const { container } = await mount({ videoUrl: 'https://example.com/lesson.mp4' })

    expect(container.querySelector('iframe.td__video')).toBeNull()
    expect(container.querySelector('a.td__link')?.getAttribute('href')).toBe('https://example.com/lesson.mp4')
  })

  it('视频：不是 https 的链接连链接都不给', async () => {
    const { container } = await mount({ videoUrl: 'http://example.com/lesson.mp4' })

    expect(container.querySelector('iframe.td__video')).toBeNull()
    expect(container.querySelector('a.td__link')).toBeNull()
  })
})
