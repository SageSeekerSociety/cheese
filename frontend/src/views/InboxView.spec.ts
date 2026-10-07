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
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listAwaitingMe: (...a: unknown[]) => listAwaitingMe(...a),
    resolveAlert: (...a: unknown[]) => resolveAlert(...a),
  }
})

const refreshProjects = vi.fn()
const store = { projectsSettled: true, projects: [] as { id: string }[], refreshProjects }
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

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
})

beforeEach(async () => {
  setLocale('zh-CN')
  store.projectsSettled = true
  store.projects = []
  listAwaitingMe.mockReset().mockResolvedValue({ data: [], total: 0 })
  refreshProjects.mockReset().mockResolvedValue(undefined)
  showNewProject.mockReset()
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/teams', name: 'HomeTeamsExplore', component: { template: '<div />' } },
      { path: '/p/:projectId/t/:topicId', name: 'workspace-topic', component: { template: '<div />' } },
      { path: '/p/:projectId/t/:topicId/tasks/:taskId', name: 'workspace-task', component: { template: '<div />' } },
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
          question: '上线前要不要先灰度',
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

  it('交不上去时这一件留着，并说出来', async () => {
    store.projects = [{ id: 'p1' }]
    listAwaitingMe.mockResolvedValue({
      data: [item({ reason: 'decide', phrase: 'decision', alertId: 7, question: '要不要灰度', options: ['先灰度'] })],
      total: 1,
    })
    resolveAlert.mockRejectedValue(new Error('无法提交'))
    await mount()
    await fireEvent.click(await screen.findByRole('button', { name: '先灰度' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('要不要灰度')).toBeTruthy()
  })
})
