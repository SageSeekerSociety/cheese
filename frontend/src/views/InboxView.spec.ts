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
import { render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listAwaitingMe = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listAwaitingMe: (...a: unknown[]) => listAwaitingMe(...a) }
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
