// 第五批那两层「包一层」留下的接缝 —— 现在只剩发题这一层。
//
// 发题底下是老页面（`spaces/detail/PublishTask.vue`），那一页一行没改，所以唯一可能
// 悄悄坏掉的地方在接缝上：那一页从 pinia 的 `space` store 拿空间，而它挂载时立刻
// `fetchCategories()`，`currentSpaceId` 为空时那方法**直接返回**（不报错）。所以
// 「装完再挂」这件事错了，界面照样打得开，只是分类下拉是空的。
//
// 所以这一条让替身替掉底下那一页，**替身自己把接缝上的东西读出来渲染到 DOM**——
// 断言的是「挂进去之后实际拿到什么」，不是「源码里写了什么」。
//
// （详情那半边原来也在这里：那一页底下是 `views/tasks/Detail.vue`，同一个「套进新外壳」
// 的形状，测的是 provide 下去的路由名。现在 `TaskDetail.vue` 自己就是那一页了，底下不再
// 有老页面，接缝上没有可测的东西 —— 那半边连同它的替身一起删了，换成 `TaskDetail.spec.ts`
// 测这一页自己的行为。第七批的整板看板也是同样的路子：换成 `Analytics.vue` 之后由
// `Analytics.spec.ts` 测。）
import type { Component } from 'vue'

import { h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TaskPublish from './TaskPublish.vue'

const spaceDetail = vi.fn()
const listCategories = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 发题底下那一页：替身报两件事 —— 发完题落到哪，以及**它挂载的这一刻**空间装好没有。
vi.mock('@/views/spaces/detail/PublishTask.vue', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  const { usePublishDoneRoute } = await import('@/lib/shellRouteNames')
  const { useSpaceStore } = await import('@/stores/space')
  return {
    default: dc({
      name: 'PublishProbe',
      setup() {
        const store = useSpaceStore()
        const doneRoute = usePublishDoneRoute()
        // 挂载的那一刻就取下来：晚一点取就测不到「装完再挂」这件事了。
        const id = store.currentSpaceId
        const categoryCount = store.categories.length
        return () =>
          hh('div', { 'data-testid': 'publish-probe' }, [
            hh('span', { 'data-testid': 'done-route' }, doneRoute),
            hh('span', { 'data-testid': 'space-id-at-mount' }, String(id)),
            hh('span', { 'data-testid': 'categories-at-mount' }, String(categoryCount)),
          ])
      },
    }),
  }
})

const SPACE_ID = 7

const stub = { render: () => h('div') }

/** 只有被测的那一层要落上去的那几条路由：这一份量的不是「地址接没接住」
 *  （那是 `../routes.spec.ts`），而是「挂进去之后底下那一页从接缝上读到什么」。 */
function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/board', name: 'SpaceBoardHome', component: stub },
      { path: '/spaces/:spaceId/board/publish', name: 'SpaceBoardTaskPublish', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
    ],
  })
}

/** 这些「包一层」的页面就是这么被挂上去的：当前路由给参数，组件直接挂 —— 所以
 *  下面不用 `router-view`，但路由照样推到那一条上，`useRoute()` 拿得到空间 id。 */
async function mountAt(path: string, component: Component) {
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  return render(component, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
}

/** 从挂载结果里读一个 testid 的文字 —— 断言都走这只小手，免得满篇 querySelector。
 *  收 `Element` 而不是 `HTMLElement`：testing-library 给的容器就是前者。 */
function textOf(container: Element, testId: string): string | null {
  return container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? null
}

describe('发题挂进新外壳', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    spaceDetail.mockImplementation(async () => ({ data: { space: { id: SPACE_ID, name: '数据结构空间' } } }))
    listCategories.mockImplementation(async () => ({ data: { categories: [{ id: 3, name: '基础题' }] } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('发题：底下那一页挂载时空间已经装好，分类也在了', async () => {
    const { container } = await mountAt(`/spaces/${SPACE_ID}/board/publish`, TaskPublish)
    // 「装完再挂」是这一层的全部意义，所以等的是**那一页出现**，不是某个数字。
    await waitFor(() => expect(container.querySelector('[data-testid="publish-probe"]')).not.toBeNull())
    expect(textOf(container, 'space-id-at-mount')).toBe(String(SPACE_ID))
    expect(textOf(container, 'categories-at-mount')).toBe('1')
  })

  it('发题：发完落到新外壳的「我的」，不是老树那一页', async () => {
    const { container } = await mountAt(`/spaces/${SPACE_ID}/board/publish`, TaskPublish)
    await waitFor(() => expect(container.querySelector('[data-testid="publish-probe"]')).not.toBeNull())
    expect(textOf(container, 'done-route')).toBe('SpaceBoardMine')
  })
})
