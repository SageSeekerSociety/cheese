/** 待办页零项目时的第一屏。
 *
 *  一个刚注册的人手上一个项目都没有，`landingForMember` 就把他放在这一页。这里锁
 *  的是他头一眼能看见什么：不是一句「这里什么都没有」，而是平台上有哪几条路可走
 *  ——建项目、用邀请码加入、去看团队——以及 AI 队友和代码仓库进项目之后在哪儿配。
 */
import type { Component } from 'vue'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listAwaitingMe = vi.fn()
const resolveAlert = vi.fn()
const markRead = vi.fn()
const markAllAlertsRead = vi.fn()
const respondToInvitation = vi.fn()
const decideMcpRun = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listAwaitingMe: (...a: unknown[]) => listAwaitingMe(...a),
    resolveAlert: (...a: unknown[]) => resolveAlert(...a),
    markRead: (...a: unknown[]) => markRead(...a),
    markAllAlertsRead: (...a: unknown[]) => markAllAlertsRead(...a),
    respondToInvitation: (...a: unknown[]) => respondToInvitation(...a),
    decideMcpRun: (...a: unknown[]) => decideMcpRun(...a),
  }
})

const teams = vi.hoisted(() => ({
  acceptInvitation: vi.fn(),
  declineInvitation: vi.fn(),
  approveJoinRequest: vi.fn(),
  rejectJoinRequest: vi.fn(),
}))
vi.mock('@/api/teams', () => ({ TeamsApi: teams }))

const refreshProjects = vi.fn()
const store = { projectsSettled: true, projects: [] as { id: string }[], refreshProjects }
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

const spaceJoin = vi.fn()
vi.mock('@/api/spaces', () => ({
  SpacesApi: { join: (...a: unknown[]) => spaceJoin(...a), list: async () => ({ spaces: [] }) },
}))

const showNewProject = vi.fn()
vi.mock('@/composables/useNewProjectDialog', () => ({
  useNewProjectDialog: () => ({ show: showNewProject, open: { value: false } }),
}))

import InboxView from './InboxView.vue'

import { setLocale, t } from '@/i18n'
import { DEFAULT_SHELL, termParams } from '@/lib/shell'

const View = InboxView as unknown as Component

let vuetify: ReturnType<typeof createVuetify>
let router: ReturnType<typeof createRouter>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 加入空间那个弹窗是 Vuetify 的浮层：它要量视口，happy-dom 里没有。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(async () => {
  setLocale('zh-CN')
  store.projectsSettled = true
  store.projects = []
  listAwaitingMe.mockReset().mockResolvedValue({ data: [], total: 0 })
  refreshProjects.mockReset().mockResolvedValue(undefined)
  respondToInvitation.mockReset().mockResolvedValue({})
  for (const call of Object.values(teams)) call.mockReset().mockResolvedValue(undefined)
  showNewProject.mockReset()
  spaceJoin.mockReset()
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/teams', name: 'HomeTeamsExplore', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: { template: '<div />' } },
      { path: '/p/:projectId/t/:topicId', name: 'workspace-topic', component: { template: '<div />' } },
      { path: '/p/:projectId/t/:topicId/tasks/:taskId', name: 'workspace-task', component: { template: '<div />' } },
      { path: '/p/:projectId/overview', name: 'workspace-overview', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
})

async function mount() {
  return render(View, {
    global: {
      plugins: [vuetify, router],
      stubs: { JoinSpaceDialog: true, NotificationFeed: true },
    },
  })
}

describe('待办页零项目时的第一屏', () => {
  it('三条起步路都在，各自一句话说清进去能得到什么', async () => {
    await mount()
    expect(await screen.findByText(t('work.startPaths.project.title', termParams(DEFAULT_SHELL)))).toBeTruthy()
    expect(screen.getByText(t('work.startPaths.invite.title'))).toBeTruthy()
    expect(screen.getByText(t('work.startPaths.teams.title'))).toBeTruthy()
  })

  it('点明 AI 队友和代码仓库进项目之后在哪儿配', async () => {
    await mount()
    expect(await screen.findByText(t('work.startPaths.settingsNote'))).toBeTruthy()
  })

  it('建项目那颗按钮走的是新建项目弹窗', async () => {
    const utils = await mount()
    await utils.findByText(t('work.startPaths.project.title', termParams(DEFAULT_SHELL)))
    await utils.getByRole('button', { name: t('navigation.newProject', termParams(DEFAULT_SHELL)) }).click()
    expect(showNewProject).toHaveBeenCalled()
  })

  // 这一页上的「用邀请码加入」和首页目录里那一行是同一件事（同一个 `useJoinSpace`）：
  // 加进去了就直接进那个空间，留在原地等于什么都没发生。
  it('用邀请码加入：加进去了就进那个空间', async () => {
    spaceJoin.mockResolvedValue({ space: { id: 9 } })
    await render(View, { global: { plugins: [vuetify, router], stubs: { NotificationFeed: true } } })
    await fireEvent.click(await screen.findByRole('button', { name: t('work.startPaths.invite.action') }))
    await fireEvent.update(await screen.findByLabelText(t('work.joinLabel')), 'abc123')
    await fireEvent.click(screen.getByRole('button', { name: t('work.joinSubmit') }))

    await waitFor(() => expect(spaceJoin).toHaveBeenCalledWith({ code: 'abc123' }))
    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/spaces/9/tasks'))
  })

  it('手上有项目的人看到的还是平常那一页，不摆起步路', async () => {
    store.projects = [{ id: 'p1' }]
    await mount()
    expect(screen.queryByText(t('work.startPaths.teams.title'))).toBeNull()
  })
})

/** 「等你处理」这一列读不到时，就地换成错误 + 重试（docs/design-system.md §3.10），
 *  而不是留一片空白装作「暂无等你处理的事项」。
 */
describe('待办页读不到待处理事项时', () => {
  it('就地显示原因和重试，而不是装作「暂无」', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockReset().mockRejectedValueOnce(new Error('服务器错误'))
    await mount()

    expect(await screen.findByText(t('home.inbox.loadFailed'))).toBeTruthy()
    expect(screen.getByText('服务器错误')).toBeTruthy()
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()
    expect(screen.queryByText(t('home.inbox.waitingEmpty'))).toBeNull()
  })

  it('点重试真的再问一遍服务端', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockReset().mockRejectedValueOnce(new Error('服务器错误'))
    await mount()
    await screen.findByText(t('home.inbox.loadFailed'))
    expect(listAwaitingMe).toHaveBeenCalledTimes(1)

    listAwaitingMe.mockResolvedValueOnce({ data: [], total: 0 })
    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    await waitFor(() => expect(listAwaitingMe).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText(t('home.inbox.loadFailed'))).toBeNull())
  })
})

/** 待办上的一件事：点下去去哪，要你拍板的就地拍板。 */
describe('待办上的一件事', () => {
  const item = (over: Record<string, unknown>) => ({
    kind: 'work',
    projectId: 'p1',
    projectName: '课程项目',
    topicId: 'c1',
    topicTitle: '前端',
    taskId: null,
    taskTitle: null,
    taskTitleSource: null,
    phrase: 'awaiting_review',
    reason: 'reviewer',
    at: '2026-10-07T10:00:00Z',
    ...over,
  })

  it('任务的事打开任务页，不是它所在的频道', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [item({ taskId: 't1', taskTitle: '分享页加访问口令' })], total: 1 })
    await mount()
    const link = (await screen.findByText('分享页加访问口令')).closest('a')
    expect(link?.getAttribute('href')).toBe('/p/p1/t/c1/tasks/t1')
  })

  it('要你拍板的：选一个就交上去，这一件随之离开清单', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [
        item({
          reason: 'decide',
          phrase: 'decision',
          alertId: 7,
          headline: '上线前要不要先灰度',
          options: ['先灰度', '直接上线'],
        }),
      ],
      total: 1,
    })
    resolveAlert.mockResolvedValue({})
    await mount()
    await fireEvent.click(await screen.findByRole('button', { name: '先灰度' }))
    expect(resolveAlert).toHaveBeenCalledWith(7, '先灰度')
    await waitFor(() => expect(screen.queryByText('上线前要不要先灰度')).toBeNull())
  })

  it('芝士写给你的变更提醒：标为已读，这一件随之离开清单', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [item({ reason: 'read', phrase: 'change_alert', alertId: 8, headline: '报名表单改了' })],
      total: 1,
    })
    markRead.mockResolvedValue({})
    await mount()
    await fireEvent.click(await screen.findByRole('button', { name: t('home.inbox.markRead') }))
    expect(markRead).toHaveBeenCalledWith(8)
    await waitFor(() => expect(screen.queryByText('报名表单改了')).toBeNull())
  })

  it('一个项目里有好几条变更提醒：一下全部标为已读，要你拍板的留着', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [
        item({ reason: 'read', phrase: 'change_alert', alertId: 8, headline: '报名表单改了' }),
        item({ reason: 'read', phrase: 'change_alert', alertId: 9, headline: '依赖升级了' }),
        item({ reason: 'decide', phrase: 'decision', alertId: 7, headline: '要不要灰度', options: ['先灰度'] }),
      ],
      total: 3,
    })
    markAllAlertsRead.mockResolvedValue({ marked: 2 })
    await mount()
    await fireEvent.click(await screen.findByRole('button', { name: t('home.inbox.markAllRead') }))
    expect(markAllAlertsRead).toHaveBeenCalledWith('p1')
    await waitFor(() => expect(screen.queryByText('报名表单改了')).toBeNull())
    expect(screen.queryByText('依赖升级了')).toBeNull()
    expect(screen.getByText('要不要灰度')).toBeTruthy()
  })

  it('不指向哪个频道的变更提醒，点开去项目总览', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [
        item({
          reason: 'read',
          phrase: 'change_alert',
          alertId: 8,
          headline: '依赖升级了',
          topicId: null,
          topicTitle: '',
        }),
      ],
      total: 1,
    })
    await mount()
    const link = (await screen.findByText('依赖升级了')).closest('a')
    expect(link?.getAttribute('href')).toBe('/p/p1/overview')
  })

  it('交不上去时这一件留着，并说出来', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [item({ reason: 'decide', phrase: 'decision', alertId: 7, headline: '要不要灰度', options: ['先灰度'] })],
      total: 1,
    })
    resolveAlert.mockRejectedValue(new Error('无法提交'))
    await mount()
    await fireEvent.click(await screen.findByRole('button', { name: '先灰度' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('要不要灰度')).toBeTruthy()
  })
})

/** 没带选项的决策请求：库里还留着以前写进来的。它得能就地答复掉，不然永远挂在清单和角标上。 */
describe('没带选项的决策请求', () => {
  const decision = {
    kind: 'work',
    projectId: 'p1',
    projectName: '课程项目',
    topicId: 'c1',
    topicTitle: '前端',
    taskId: null,
    taskTitle: null,
    taskTitleSource: null,
    phrase: 'decision',
    reason: 'decide',
    at: '2026-10-07T10:00:00Z',
    alertId: 11,
    headline: '报名表单要不要加验证码',
    options: [],
  }

  it('写一句答复交上去，这一件随之离开清单', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [decision], total: 1 })
    resolveAlert.mockResolvedValue({})
    await mount()
    const reply = await screen.findByRole('button', { name: t('home.inbox.answer') })
    await fireEvent.update(screen.getByRole('textbox', { name: t('home.inbox.answerLabel') }), '先不强制')
    await fireEvent.click(reply)
    expect(resolveAlert).toHaveBeenCalledWith(11, '先不强制')
    await waitFor(() => expect(screen.queryByText('报名表单要不要加验证码')).toBeNull())
  })

  it('什么都没写时不交空答复', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [decision], total: 1 })
    resolveAlert.mockReset()
    await mount()
    await fireEvent.click(await screen.findByRole('button', { name: t('home.inbox.answer') }))
    expect(resolveAlert).not.toHaveBeenCalled()
  })
})

/** 等你回答的邀请和加入申请：在「等你处理」里，就地回答，答完离开清单。 */
describe('等你回答的邀请和申请', () => {
  const person = { name: '王晨曦', handle: 'wangcx', avatarUrl: null }
  const ask = (over: Record<string, unknown>) => ({
    kind: 'project_invite',
    id: 'inv-1',
    placeId: 'p9',
    place: { name: '课程设计', handle: null, avatarUrl: null },
    person,
    role: null,
    message: '',
    at: '2026-10-07T10:00:00Z',
    ...over,
  })

  it('接受项目邀请：答复交上去，这一行离开清单，左边那一列重读项目', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [ask({})], total: 1 })
    await mount()
    await screen.findByText('课程设计')

    await fireEvent.click(screen.getByRole('button', { name: t('home.inbox.invite.accept') }))

    expect(respondToInvitation).toHaveBeenCalledWith('inv-1', true)
    await waitFor(() => expect(screen.queryByText('课程设计')).toBeNull())
    expect(refreshProjects).toHaveBeenCalled()
  })

  it('拒绝团队邀请：交的是拒绝，这一行离开清单', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [
        ask({
          kind: 'team_invite',
          id: '42',
          placeId: '7',
          place: { name: '知是', handle: 'zhishi', avatarUrl: null },
        }),
      ],
      total: 1,
    })
    await mount()
    await screen.findByText('知是')

    await fireEvent.click(screen.getByRole('button', { name: t('home.inbox.invite.decline') }))

    expect(teams.declineInvitation).toHaveBeenCalledWith(42)
    expect(teams.acceptInvitation).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByText('知是')).toBeNull())
  })

  it('同意加入申请：按申请的那个团队交上去', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [
        ask({
          kind: 'join_request',
          id: '55',
          placeId: '7',
          place: { name: '知是', handle: 'zhishi', avatarUrl: null },
          message: '想参与前端',
        }),
      ],
      total: 1,
    })
    await mount()
    expect(await screen.findByText('想参与前端')).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: t('home.inbox.invite.approve') }))

    expect(teams.approveJoinRequest).toHaveBeenCalledWith(7, 55)
    await waitFor(() => expect(screen.queryByText('想参与前端')).toBeNull())
  })

  it('交不上去时这一行留着', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [ask({})], total: 1 })
    respondToInvitation.mockRejectedValue(new Error('网络错误'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    await mount()
    await screen.findByText('课程设计')

    await fireEvent.click(screen.getByRole('button', { name: t('home.inbox.invite.accept') }))

    await waitFor(() => expect(respondToInvitation).toHaveBeenCalled())
    expect(screen.getByText('课程设计')).toBeTruthy()
    expect(refreshProjects).not.toHaveBeenCalled()
  })
})

/** 项目要在你自己的电脑上运行一条命令：在「等你处理」里，就地允许或不允许，答完离开清单。 */
describe('等你允许在自己电脑上运行的命令', () => {
  const run = {
    kind: 'mcp_run',
    id: 'run-1',
    projectId: 'p9',
    projectName: '标注工具选型',
    server: 'zotero',
    command: 'uvx zotero-mcp',
    device: 'MacBook Pro',
    source: 'repository',
    author: null,
    at: '2026-10-07T10:00:00Z',
  }

  it('写明要在哪台电脑上运行什么命令', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [run], total: 1 })
    await mount()
    expect(await screen.findByText('uvx zotero-mcp')).toBeTruthy()
    expect(screen.getByText(/MacBook Pro/)).toBeTruthy()
  })

  it('不允许：答复交上去，这一行离开清单', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [run], total: 1 })
    decideMcpRun.mockResolvedValue({ id: 'run-1', allowed: false })
    await mount()
    await screen.findByText('uvx zotero-mcp')

    await fireEvent.click(screen.getByRole('button', { name: t('home.inbox.mcpRun.refuse') }))

    expect(decideMcpRun).toHaveBeenCalledWith('run-1', false)
    await waitFor(() => expect(screen.queryByText('uvx zotero-mcp')).toBeNull())
  })

  it('交不上去时这一行留着，并说出来', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({ data: [run], total: 1 })
    decideMcpRun.mockRejectedValue(new Error('网络错误'))
    await mount()
    await screen.findByText('uvx zotero-mcp')

    await fireEvent.click(screen.getByRole('button', { name: t('home.inbox.mcpRun.allow') }))

    await waitFor(() => expect(screen.getByText('网络错误')).toBeTruthy())
    expect(screen.getByText('uvx zotero-mcp')).toBeTruthy()
  })
})
