// 新建项目时问的那一句「你打算做什么」(#946 片 C)要真的跟着请求走，
// 否则它就是一块装饰：人认真写完，项目建出来，房间里什么也没有。
//
// 三件事：答了就发出去；没答照旧建（这一问不是必填）；上一次的答案不会
// 跟到下一个项目——那会让第二个项目凭空继承第一个项目的说明。
import { createApp, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { createPinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { createDialogPlugin } from './plugins/dialog'
import App from './App.vue'
import i18n, { setLocale } from './i18n'

import * as api from '@/api'

vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  listProjects: vi.fn(async () => ({ data: [] })),
  listProjectAgents: vi.fn(async () => ({ data: [] })),
  createProject: vi.fn(async (name: string) => ({
    id: 'p9',
    name,
    created_at: '2026-09-20T00:00:00Z',
    root_topic_id: 't9',
  })),
  // 这一页挂的是真 App 外壳（要的就是它挂载出来的那个对话框），而外壳自己会拉
  // 通知计数、词表和额度提示：`setup-network.ts` 让任何没被 mock 的请求判失败，
  // 所以这些跟「你要做什么」无关的请求也得在这里给出答案。
  getFeedbackCounts: vi.fn(async () => ({ all: 0, hot: 0, active: 0, resolved: 0, unread: 0 })),
  getFeedbackMeta: vi.fn(async () => ({ is_admin: false, hot_min_items: 5 })),
  getResourceLimits: vi.fn(async () => ({ max_machines_per_team: 0, max_concurrent_turns: 0 })),
}))
// 版本徽章自己会打 `/api/version`，而这里没有要证的东西在它身上；和
// `App.navigation.spec.ts` / `App.keepAlive.spec.ts` 同一处理。
vi.mock('@/components/common/VersionBadge.vue', () => ({ default: { template: '<span />' } }))
// 对话框里的「所属团队」要有一个选项，创建按钮才不是灰的。
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMyTeams: vi.fn(async () => ({
      data: { teams: [{ id: 7, name: '个人', personal: true }] },
    })),
  },
}))

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  // 新建项目对话框是一个 VOverlay，而 happy-dom 没有 visualViewport：不补上，
  // 弹窗根本挂不起来，测到的就成了「按了创建什么也没发生」。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())
beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(api.createProject).mockClear()
})

const blank = { template: '<div>Other page</div>' }

async function settle() {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

async function mountApp() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: blank },
      { path: '/spaces', component: blank },
      { path: '/:pathMatch(.*)*', component: blank },
    ],
  })
  const vuetify = createVuetify({ components, directives })
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp(App)
  app.use(vuetify).use(createPinia()).use(router).use(i18n).use(createDialogPlugin)
  app.mount(container)
  await router.push('/spaces')
  await settle()
  return {
    dispose: () => {
      app.unmount()
      container.remove()
    },
  }
}

/** Type into a Vuetify field the way a keyboard does: set the value, then say so. */
function type(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  field.value = value
  field.dispatchEvent(new Event('input'))
}

function inDialog(selector: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`.v-dialog ${selector}`)
  if (!el) throw new Error(`新建项目对话框里没有 ${selector}`)
  return el
}

function button(label: string): HTMLElement {
  const found = Array.from(document.querySelectorAll<HTMLElement>('.v-dialog button')).find(
    (b) => b.textContent?.trim() === label
  )
  if (!found) throw new Error(`新建项目对话框里没有「${label}」按钮`)
  return found
}

async function openDialog() {
  useNewProjectDialog().show()
  // 打开之后还要等团队列表回来，创建按钮才会亮。
  await settle()
  await settle()
}

/** 走完对话框剩下的路。它是两步的（#1436）：第 1 步填项目，「你打算做什么」就在
 *  这一步，第 2 步给这位队友取名。所以按下「创建项目」之前必须先过第 1 步——
 *  这也正是这些用例想证的：人在第 1 步写的答案，要活着走到第 2 步发出的那个请求里。 */
async function submitDialog() {
  button('下一步').click()
  await settle()
  button('创建项目').click()
  await settle()
}

it('carries what the person said into the create request', async () => {
  const harness = await mountApp()
  try {
    await openDialog()
    type(inDialog('input') as HTMLInputElement, '这学期的课')
    type(inDialog('textarea') as HTMLTextAreaElement, '帮我把这学期的课程材料整理成一份大纲')
    await settle()
    await submitDialog()

    expect(vi.mocked(api.createProject)).toHaveBeenCalledTimes(1)
    const args = vi.mocked(api.createProject).mock.calls[0]
    expect(args[0]).toBe('这学期的课')
    // createProject(name, ownerHandle, teamId, externalTaskId, forgeKind, intent, agentName)
    // ——「你打算做什么」是第 6 个参数。它和 agentName 都是主分支后加的，都排在
    // forgeKind 之后，所以这个下标跟着参数表走，别把它当成「第几个参数」的巧合。
    expect(args[5]).toBe('帮我把这学期的课程材料整理成一份大纲')
  } finally {
    harness.dispose()
  }
})

it('creates the project with an empty answer when the question was skipped', async () => {
  const harness = await mountApp()
  try {
    await openDialog()
    type(inDialog('input') as HTMLInputElement, '没答这一问的项目')
    await settle()
    await submitDialog()

    expect(vi.mocked(api.createProject)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.createProject).mock.calls[0][5]).toBe('')
  } finally {
    harness.dispose()
  }
})

it('does not let one project inherit the previous answer', async () => {
  const harness = await mountApp()
  try {
    await openDialog()
    type(inDialog('textarea') as HTMLTextAreaElement, '第一个项目要做的事')
    await settle()
    button('取消').click()
    await settle()

    await openDialog()
    expect((inDialog('textarea') as HTMLTextAreaElement).value).toBe('')

    type(inDialog('input') as HTMLInputElement, '第二个项目')
    await settle()
    await submitDialog()

    expect(vi.mocked(api.createProject).mock.calls[0][5]).toBe('')
  } finally {
    harness.dispose()
  }
})
