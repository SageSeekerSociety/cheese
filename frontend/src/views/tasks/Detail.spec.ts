// 题目详情这一层：题目名那一行的主操作、页签、右栏。量的是几条谁都能说出来的规矩：
//
// - 领取那颗按钮点不点得动，由接口给的事实决定（审核、截止、开始、人数、我的申请）；
// - 领了之后主操作变成交作业，多出「我的提交」；
// - 领取者和数据两个页签只给出题人和管理员，别人连名单接口都不问；
// - 领了才能用这道题新建项目；
// - 我的进度按我最新那一版提交与它的评审算。
//
// 接口在 `@/network/api/*` 那一层换掉：真 axios 会被 `src/test/setup-network.ts` 逮住。
// 页签内容是子路由，这里用空壳顶上 —— 它们各有各的测试。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const taskDetail = vi.fn()
const listSubmissions = vi.fn()
const getParticipants = vi.fn()
const createSubmission = vi.fn()
const updateParticipant = vi.fn()
const postSubmissionReview = vi.fn()
const getSubmissionQueue = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    detail: (...a: unknown[]) => taskDetail(...a),
    listSubmissions: (...a: unknown[]) => listSubmissions(...a),
    getParticipants: (...a: unknown[]) => getParticipants(...a),
    createSubmission: (...a: unknown[]) => createSubmission(...a),
    updateParticipant: (...a: unknown[]) => updateParticipant(...a),
    postSubmissionReview: (...a: unknown[]) => postSubmissionReview(...a),
  },
}))
vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { getSubmissionQueue: (...a: unknown[]) => getSubmissionQueue(...a) },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 「我的进度」里的项目：从这道题开出来的项目，以及「用这道题新建项目」。
const listProjectsForTask = vi.fn()
vi.mock('@/api', async (original) => ({
  ...(await original<object>()),
  listProjectsForTask: (...a: unknown[]) => listProjectsForTask(...a),
}))
const showNewProjectDialog = vi.fn()
vi.mock('@/composables/useNewProjectDialog', () => ({
  useNewProjectDialog: () => ({ show: (...a: unknown[]) => showNewProjectDialog(...a) }),
}))

vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 交作业表单开场问一次单个文件的上限，写进「提交须知」。
vi.mock('@/network/api/attachments', () => ({
  AttachmentsApi: { upload: vi.fn(), limits: vi.fn(async () => ({ data: { maxFileBytes: 100 * 1024 * 1024 } })) },
}))

// 领取/退队/实名/对话那几张对话框挂在老机器上（事件总线 + `TaskDialogs`），这里只留空壳。
vi.mock('@/views/tasks/components', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  return {
    TaskDialogs: dc({
      name: 'TaskDialogsStub',
      props: ['taskData', 'availableTeams', 'loadingTeams', 'joinedTeams', 'selectedLeaveTeamId', 'participationInfo'],
      setup: () => () => hh('div'),
    }),
  }
})

import TaskRoster from './detail/Roster.vue'
import TaskSubmit from './detail/Submit.vue'
import TaskDetail from './Detail.vue'

import i18n, { setLocale } from '@/i18n'
import AccountService from '@/services/account'
import { useEvents } from '@/views/tasks/events'

const SPACE_ID = 7
const TASK_ID = 42
const DAY = 86_400_000
const BASE = `/spaces/${SPACE_ID}/tasks/${TASK_ID}`

const BASE_TASK = {
  id: TASK_ID,
  name: '把红黑树插一遍',
  intro: '写出每一步旋转',
  description: '**要求**：写出每一步旋转',
  approved: 'APPROVED',
  rejectReason: '',
  category: { id: 3, name: '基础题' },
  creator: { id: 1, username: 'cai', nickname: '蔡松洋' },
  topics: [],
  videoUrl: '',
  participants: { total: 0, examples: [] },
  participantLimit: 0,
  deadline: null as number | null,
  registrationStartAt: null as number | null,
  submitterType: 'USER',
  minTeamSize: 1,
  maxTeamSize: 1,
  resubmittable: true,
  createdAt: Date.now() - 3 * DAY,
  space: { id: SPACE_ID, name: '数据结构空间', admins: [] },
}

const APPROVED_ME = { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved: 'APPROVED' }] }

function payload(task: Record<string, unknown> = {}, participation: Record<string, unknown> = {}) {
  return {
    data: {
      task: { ...BASE_TASK, ...task },
      participation: { hasParticipation: false, identities: [], ...participation },
    },
  }
}

const stub = { render: () => null }

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: stub },
      {
        path: '/spaces/:spaceId/tasks/:taskId',
        component: TaskDetail,
        children: [
          { path: '', name: 'TasksDetail', component: stub },
          { path: 'submissions', name: 'TasksSubmissions', component: stub },
          // 交作业是真表单：交上之后外框（我的进度、页签上的版本号）跟不跟得上，要它来量。
          { path: 'submit', name: 'TasksSubmit', component: TaskSubmit },
          // 领取者也是真页签：出题人在这里批了领取、判了一版，页头和我的进度跟不跟得上，要它来量。
          { path: 'participants', name: 'TasksParticipants', component: TaskRoster },
          { path: 'insights', name: 'TasksInsights', component: stub },
        ],
      },
      { path: '/spaces/:spaceId/tasks/:taskId/edit', name: 'TasksEdit', component: stub },
      { path: '/projects/:projectId', name: 'project', component: stub },
      { path: '/teams', name: 'HomeTeamsMine', component: stub },
    ],
  })
}

async function mount(
  task: Record<string, unknown> = {},
  participation: Record<string, unknown> = {},
  path: string = BASE
) {
  taskDetail.mockImplementation(async () => payload(task, participation))
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const utils = render(
    { template: '<router-view />' },
    {
      global: {
        plugins: [createVuetify({ components, directives }), router, createPinia(), i18n],
        // 对话框原地摊开：jsdom 没有 visualViewport，浮层一挂就抛；开着的时候里面的东西照常画。
        stubs: {
          VDialog: {
            props: ['modelValue'],
            template: '<div v-if="modelValue"><slot /></div>',
          },
        },
      },
    }
  )
  await waitFor(() => expect(utils.container.querySelector('h1')?.textContent).toContain('把红黑树插一遍'))
  return { ...utils, router }
}

const t = (key: string) => i18n.global.t(key)

function claimButton(container: Element) {
  return container.querySelector<HTMLButtonElement>('[data-testid="task-claim"]')
}

function hrefs(container: Element): string[] {
  return Array.from(container.querySelectorAll('a')).map((a) => a.getAttribute('href') ?? '')
}

/** 字正好是这一句的那颗按钮：领取者页签上的筛选也是按钮（「待批准领取」「待评审」），按包含找会找错。 */
function buttonNamed(scope: Element, text: string): HTMLButtonElement | null {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === text) ?? null
}

function buttonWith(scope: Element, text: string): HTMLButtonElement | null {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.includes(text)) ?? null
}

describe('题目详情', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    setActivePinia(createPinia())
    listProjectsForTask.mockResolvedValue({ data: [] })
    // 默认是「路人」：非出题人、非管理员。
    AccountService.user = null
    listSubmissions.mockImplementation(async () => ({ data: { submissions: [], page: {} } }))
    getParticipants.mockImplementation(async () => ({ data: { participants: [] } }))
    getSubmissionQueue.mockImplementation(async () => ({ data: { submissions: [] } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('审过了、没截止、没满：领取按钮点得动，点了走领取那条路', async () => {
    const { container } = await mount({ participants: { total: 1, examples: [] }, participantLimit: 5 })
    const events = useEvents()
    const joined = vi.fn()
    events.on('join-clicked', joined)

    const btn = claimButton(container)!
    expect(btn.disabled).toBe(false)
    await fireEvent.click(btn)
    expect(joined).toHaveBeenCalled()
  })

  it.each([
    ['题目还在审', { approved: 'NONE' }, {}],
    ['题目被驳回', { approved: 'DISAPPROVED' }, {}],
    ['已截止', { deadline: Date.now() - DAY }, {}],
    ['还没开始', { registrationStartAt: Date.now() + DAY }, {}],
    ['人数已满', { participants: { total: 3, examples: [] }, participantLimit: 3 }, {}],
    ['我的申请还在等批', {}, { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved: 'NONE' }] }],
    ['我的申请被拒了', {}, { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved: 'DISAPPROVED' }] }],
  ])('%s：领取按钮点不动', async (_, task, participation) => {
    const { container } = await mount(task, participation)

    expect(claimButton(container)?.disabled).toBe(true)
  })

  it.each([
    ['个人', { submitterType: 'USER' }, { id: 11, type: 'USER', approved: 'NONE' }],
    ['团队', { submitterType: 'TEAM' }, { id: 12, type: 'TEAM', approved: 'NONE', teamName: '红队' }],
  ])('%s领取还在等批：没有「提交作业」，按钮灰着写明在等批，也没有「我的提交」', async (_, task, identity) => {
    // 接口对等批的报名也回 `joined: true`，只有 `submittable` 是 false。
    const { container } = await mount(
      { ...task, joined: true, submittable: false },
      { hasParticipation: true, identities: [identity] }
    )

    expect(buttonWith(container, t('tasks.page.submit.first'))).toBeNull()
    expect(hrefs(container)).not.toContain(`${BASE}/submit`)
    const btn = claimButton(container)!
    expect(btn.disabled).toBe(true)
    expect(btn.textContent).toContain(t('tasks.page.claim.pending'))
    expect(hrefs(container)).not.toContain(`${BASE}/submissions`)
  })

  it('批过之后：主操作是「提交作业」', async () => {
    const { container } = await mount({ joined: true, submittable: true }, APPROVED_ME)

    await waitFor(() => expect(hrefs(container)).toContain(`${BASE}/submit`))
    expect(claimButton(container)).toBeNull()
  })

  it('上限 0 是「不限」：人再多也领得动', async () => {
    const { container } = await mount({ participants: { total: 30, examples: [] }, participantLimit: 0 })

    expect(claimButton(container)?.disabled).toBe(false)
  })

  it('领了之后：没有领取按钮，主操作是去交作业，多出「我的提交」', async () => {
    const { container } = await mount({}, APPROVED_ME)

    expect(claimButton(container)).toBeNull()
    await waitFor(() => expect(hrefs(container)).toContain(`${BASE}/submit`))
    expect(hrefs(container)).toContain(`${BASE}/submissions`)
  })

  it('只能交一次、而且已经交过：不再给交作业的入口', async () => {
    listSubmissions.mockImplementation(async () => ({
      data: { submissions: [{ id: 88, version: 1, review: { reviewed: false } }], page: {} },
    }))
    const { container } = await mount({ resubmittable: false }, APPROVED_ME)

    await waitFor(() => expect(listSubmissions).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(hrefs(container)).not.toContain(`${BASE}/submit`)
  })

  it('题目已结束：不再给交作业的入口', async () => {
    const { container } = await mount({ endedAt: Date.now() - DAY }, APPROVED_ME)

    await waitFor(() => expect(listSubmissions).toHaveBeenCalled())
    expect(hrefs(container)).not.toContain(`${BASE}/submit`)
  })

  it('我的进度按我那一份最新一版提交与它的评审算', async () => {
    listSubmissions.mockImplementation(async () => ({
      data: { submissions: [{ id: 88, version: 3, review: { reviewed: true, detail: { accepted: true } } }], page: {} },
    }))
    const { container } = await mount({}, APPROVED_ME)

    expect(listSubmissions).toHaveBeenCalledWith(TASK_ID, 11, {
      pageSize: 1,
      sort_by: 'createdAt',
      sort_order: 'desc',
      queryReview: true,
    })
    await waitFor(() => expect(container.textContent).toContain(i18n.global.t('tasks.side.versionPassed', { n: 3 })))
  })

  it('交上一版之后，我的进度和「我的提交」上的版本号当场跟上，不用刷新', async () => {
    let versions: { id: number; version: number; review: { reviewed: boolean } }[] = []
    listSubmissions.mockImplementation(async () => ({ data: { submissions: versions, page: {} } }))
    createSubmission.mockImplementation(async () => {
      versions = [{ id: 90, version: 1, review: { reviewed: false } }]
      return {}
    })
    const { container, getByRole } = await mount(
      { submittable: true, submissionSchema: [{ type: 'TEXT', prompt: '成果说明' }] },
      { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved: 'APPROVED', canSubmit: true }] },
      `${BASE}/submit`
    )
    await waitFor(() => expect(container.textContent).toContain(t('tasks.side.noSubmission')))

    await fireEvent.update(getByRole('textbox', { name: '成果说明' }), '旋转了三次')
    await fireEvent.submit(container.querySelector('form')!)

    await waitFor(() => expect(createSubmission).toHaveBeenCalledWith(TASK_ID, 11, [{ text: '旋转了三次' }]))
    await waitFor(() => expect(container.textContent).toContain(i18n.global.t('tasks.side.versionPending', { n: 1 })))
    const mineTab = Array.from(container.querySelectorAll('a')).find((a) =>
      a.getAttribute('href')?.endsWith('/submissions')
    )
    expect(mineTab?.querySelector('.td__tab-count')?.textContent).toBe('1')
  })

  it('出题人在「领取者」里批了自己的领取：回到「说明」，页头已是「提交作业」，不用刷新', async () => {
    AccountService.user = { id: 1, nickname: '蔡松洋' } as never
    let approved = 'NONE'
    getParticipants.mockImplementation(async () => ({
      data: {
        participants: [
          { id: 11, member: { id: 1, name: '蔡松洋', intro: '', avatarId: null }, createdAt: Date.now(), approved },
        ],
      },
    }))
    updateParticipant.mockImplementation(async () => {
      approved = 'APPROVED'
      return {}
    })
    const { container, router } = await mount(
      { joined: true, submittable: false, participants: { total: 1, examples: [] } },
      { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved: 'NONE' }] },
      `${BASE}/participants`
    )
    // 批准之后再问，接口说的就是批过的那一份。
    taskDetail.mockImplementation(async () =>
      payload(
        { joined: true, submittable: approved === 'APPROVED', participants: { total: 1, examples: [] } },
        { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved }] }
      )
    )
    await waitFor(() => expect(buttonNamed(container, t('tasks.roster.approve'))).not.toBeNull())

    await fireEvent.click(buttonNamed(container, t('tasks.roster.approve'))!)
    await waitFor(() => expect(updateParticipant).toHaveBeenCalled())
    await router.push(BASE)

    await waitFor(() => expect(hrefs(container)).toContain(`${BASE}/submit`))
    expect(claimButton(container)).toBeNull()
    expect(container.textContent).not.toContain(t('tasks.page.claim.pending'))
    expect(container.textContent).toContain(t('tasks.side.noSubmission'))
  })

  it('出题人在「领取者」里判了自己那一版：回到「说明」，我的进度写的是判过的结果', async () => {
    AccountService.user = { id: 1, nickname: '蔡松洋' } as never
    let review: Record<string, unknown> = { reviewed: false }
    const version = () => ({
      id: 90,
      version: 1,
      participantId: 11,
      createdAt: Date.now(),
      content: [],
      submitter: { nickname: '蔡松洋' },
      review,
    })
    listSubmissions.mockImplementation(async () => ({
      data: { submissions: [version()], page: { page_start: 0, page_size: 10, has_more: false, total: 1 } },
    }))
    getParticipants.mockImplementation(async () => ({
      data: {
        participants: [
          {
            id: 11,
            member: { id: 1, name: '蔡松洋', intro: '', avatarId: null },
            createdAt: Date.now(),
            approved: 'APPROVED',
          },
        ],
      },
    }))
    getSubmissionQueue.mockImplementation(async () => ({ data: { submissions: [version()] } }))
    postSubmissionReview.mockImplementation(async () => {
      review = { reviewed: true, detail: { accepted: false, score: 0, comment: '' } }
      return {}
    })
    const { container, router } = await mount(
      { joined: true, submittable: true, participants: { total: 1, examples: [] } },
      APPROVED_ME
    )
    await waitFor(() => expect(container.textContent).toContain(i18n.global.t('tasks.side.versionPending', { n: 1 })))

    await router.push(`${BASE}/participants`)
    await waitFor(() => expect(buttonNamed(container, t('tasks.roster.review'))).not.toBeNull())
    await fireEvent.click(buttonNamed(container, t('tasks.roster.review'))!)
    const reject = await waitFor(() => {
      const radio = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="radio"]')).find(
        (r) => r.value === 'false'
      )
      expect(radio).toBeTruthy()
      return radio!
    })
    reject.checked = true
    await fireEvent.input(reject)
    await fireEvent.change(reject)
    await fireEvent.click(buttonWith(container, t('tasks.submissionHistory.submit'))!)
    await waitFor(() => expect(postSubmissionReview).toHaveBeenCalled())
    await router.push(BASE)

    await waitFor(() => expect(container.textContent).toContain(i18n.global.t('tasks.side.versionFailed', { n: 1 })))
    expect(container.textContent).not.toContain(i18n.global.t('tasks.side.versionPending', { n: 1 }))
  })

  it('不是出题人：领取者和数据两个页签都没有，名单接口也不问', async () => {
    const { container } = await mount({ participants: { total: 3, examples: [] } })

    expect(getParticipants).not.toHaveBeenCalled()
    expect(hrefs(container)).not.toContain(`${BASE}/participants`)
    expect(hrefs(container)).not.toContain(`${BASE}/insights`)
  })

  it('出题人：领取者和数据两个页签都在', async () => {
    AccountService.user = { id: 1, nickname: '蔡松洋' } as never
    const { container } = await mount()

    expect(hrefs(container)).toContain(`${BASE}/participants`)
    expect(hrefs(container)).toContain(`${BASE}/insights`)
  })

  it('没领这道题：不能用它新建项目', async () => {
    const { container } = await mount()

    expect(buttonWith(container, t('tasks.side.newProject'))).toBeNull()
    expect(listProjectsForTask).not.toHaveBeenCalled()
  })

  it('领了这道题：用它新建项目，带着这道题；已经开出来的项目点得进去', async () => {
    listProjectsForTask.mockResolvedValue({ data: [{ id: 'p-1', name: '红黑树小组的项目' }] })
    const { container } = await mount({}, APPROVED_ME)

    await waitFor(() => expect(hrefs(container)).toContain('/projects/p-1'))
    await fireEvent.click(buttonWith(container, t('tasks.side.newProject'))!)
    expect(showNewProjectDialog).toHaveBeenCalledWith(null, { id: TASK_ID, name: '把红黑树插一遍' })
  })

  it('用团队领的：新项目挂在那个团队下', async () => {
    const { container } = await mount(
      { submitterType: 'TEAM' },
      {
        hasParticipation: true,
        identities: [{ id: 11, type: 'TEAM', memberId: 55, teamName: '红黑树小组', approved: 'APPROVED' }],
      }
    )

    await waitFor(() => expect(buttonWith(container, t('tasks.side.newProject'))).not.toBeNull())
    await fireEvent.click(buttonWith(container, t('tasks.side.newProject'))!)
    expect(showNewProjectDialog).toHaveBeenCalledWith(55, { id: TASK_ID, name: '把红黑树插一遍' })
  })

  it('领不了的原因说中文：认得出的按原因说，认不出的给一句通用的，不露后端的英文', async () => {
    const { container } = await mount({
      participationEligibility: {
        user: { eligible: false, reasons: [{ code: 'TASK_NOT_APPROVED', message: 'Task is not approved yet.' }] },
      },
    })
    expect(container.textContent).not.toContain('Task is not approved yet.')
    expect(container.textContent).toContain(t('tasks.eligibility.TASK_NOT_APPROVED'))

    cleanup()
    const other = await mount({
      participationEligibility: {
        user: { eligible: false, reasons: [{ code: 'SOMETHING_NEW', message: 'Something new happened.' }] },
      },
    })
    expect(other.container.textContent).not.toContain('Something new happened.')
    expect(other.container.textContent).toContain(t('tasks.eligibility.unknown'))
  })

  it('我的进度写的是我这份报名自己的截止，按我的钟点到分钟；题目的截止不顶替它', async () => {
    // 本地时间造出来的时刻：不管测试机在哪个时区，这一刻在本地都是 10 月 24 日 16:05。
    const mine = new Date(2030, 9, 24, 16, 5).getTime()
    const closes = new Date(2030, 9, 26, 9, 0).getTime()
    const { container } = await mount(
      { deadline: closes, userDeadline: closes },
      { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved: 'APPROVED', deadline: mine }] }
    )

    const line = container.querySelector('[data-testid="my-deadline"]')
    expect(line?.textContent).toContain('10月24日 16:05')
    expect(line?.textContent).not.toContain('10月26日')
  })

  it('还在等批准的报名没有自己的截止，不写那一行', async () => {
    const { container } = await mount(
      { deadline: new Date(2030, 9, 26, 9, 0).getTime() },
      { hasParticipation: true, identities: [{ id: 11, type: 'USER', approved: 'NONE', deadline: null }] }
    )
    expect(container.querySelector('[data-testid="my-deadline"]')).toBeNull()
  })

  it('提交期限按天说：老数据里存的毫秒也换成天', async () => {
    const { container } = await mount({ defaultDeadline: 14 * DAY })
    expect(container.textContent).toContain(i18n.global.t('tasks.side.periodValue', { n: 14 }))

    cleanup()
    const days = await mount({ defaultDeadline: 30 })
    expect(days.container.textContent).toContain(i18n.global.t('tasks.side.periodValue', { n: 30 }))
  })
})
