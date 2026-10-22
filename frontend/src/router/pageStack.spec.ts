// @vitest-environment jsdom
//
// 手机冷开落在页面栈中间的一页时，身后要有一条来路：安卓的系统返回键走的是浏览器
// 历史，历史里只有落点这一条时，它没有上一层可退，一按就把应用关掉。
//
// 用 jsdom，因为要的是一套真的浏览器历史（back / popstate / state）——happy-dom 的
// History 是空壳，退一步什么都不会发生。
import type { Router } from 'vue-router'

import { createRouter, createWebHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ensureStackBehind } from './pageStack'

const blank = { template: '<div />' }

function makeRouter(): Router {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'HomeDefault', component: blank },
      { path: '/inbox', name: 'inbox', component: blank },
      {
        path: '/projects/:projectId',
        name: 'workspace-project',
        component: blank,
      },
      {
        path: '/projects/:projectId/channels/:topicId',
        name: 'workspace-topic',
        component: blank,
        meta: { backTo: 'workspace-project' },
      },
      {
        path: '/projects/:projectId/channels/:topicId/threads/:threadId',
        name: 'workspace-thread',
        component: blank,
        meta: { backTo: 'workspace-topic' },
      },
      {
        path: '/projects/:projectId/tasks/:taskId',
        name: 'workspace-task',
        component: blank,
        meta: { backTo: 'workspace-project' },
      },
    ],
  })
}

/** 手机外壳：`(min-width: 960px)` 不匹配。 */
function phone() {
  stubWidth(false)
}

function stubWidth(desktop: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: query.includes('min-width') ? desktop : !desktop,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

async function settle() {
  for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 5))
}

async function browserBack() {
  window.history.back()
  await settle()
}

const back = (router: Router) => router.options.history.state?.back

beforeEach(() => {
  // 每个用例都从「刚打开应用、历史里只有这一条」开始：jsdom 的历史跨用例累积，
  // 而 vue-router 只在自己的 state 为空时才重新起一条。
  window.history.replaceState(null, '', '/')
  phone()
})

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia
})

describe('冷开落在页面栈中间', () => {
  it('任务页身后垫着项目首页：返回键退到那里，不是退出应用', async () => {
    const router = makeRouter()
    await router.push('/projects/p/tasks/318')
    // 冷开的样子：落点就是历史里的第一条，身后没有来路。
    expect(back(router)).toBeNull()

    await ensureStackBehind(router)

    expect(router.currentRoute.value.fullPath).toBe('/projects/p/tasks/318')
    expect(back(router)).toBe('/projects/p')

    // 系统返回键走的就是这一步。
    await browserBack()
    expect(router.currentRoute.value.fullPath).toBe('/projects/p')
  })

  // 支线是两层栈：支线 → 频道 → 频道列表。垫的也是这两层，一层一层退得回去。
  it('支线页身后垫着频道和项目首页', async () => {
    const router = makeRouter()
    await router.push('/projects/p/channels/t9/threads/x1')

    await ensureStackBehind(router)

    expect(router.currentRoute.value.fullPath).toBe('/projects/p/channels/t9/threads/x1')
    expect(back(router)).toBe('/projects/p/channels/t9')

    await browserBack()
    expect(router.currentRoute.value.fullPath).toBe('/projects/p/channels/t9')
    await browserBack()
    expect(router.currentRoute.value.fullPath).toBe('/projects/p')
  })

  // 落在底栏那一级的根上：它本来就是最外面那一层，返回键从这里退出应用是对的。
  it('落在项目首页时不垫任何东西', async () => {
    const router = makeRouter()
    await router.push('/projects/p')

    await ensureStackBehind(router)

    expect(back(router)).toBeNull()
    expect(router.currentRoute.value.fullPath).toBe('/projects/p')
  })

  // 应用里跳过来的那一页身后已经有真正的来路，垫上去就把它盖掉了。
  it('身后已经有来路时不动它', async () => {
    const router = makeRouter()
    await router.push('/inbox')
    await router.push('/projects/p/tasks/318')
    expect(back(router)).toBe('/inbox')

    await ensureStackBehind(router)

    expect(back(router)).toBe('/inbox')
    await browserBack()
    expect(router.currentRoute.value.fullPath).toBe('/inbox')
  })

  // 桌面上浏览器的返回本来就该离开这个站，那是浏览器的规矩，不是要修的毛病。
  it('桌面上不垫', async () => {
    stubWidth(true)
    const router = makeRouter()
    await router.push('/projects/p/tasks/318')

    await ensureStackBehind(router)

    expect(back(router)).toBeNull()
  })
})
