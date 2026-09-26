// 第五批那两层「包一层」的接缝。
//
// 这两页底下都是老页面（详情、发题），页面本身一行没改，所以唯一可能悄悄坏掉的
// 地方在接缝上：
//
// - **详情**：provide 下去的路由名得是新树那一套。漏了不会报错 —— 只是在新外壳里
//   点「提交记录」会把人连同页面送回老树。
// - **发题**：那一页从 pinia 的 `space` store 拿空间，而它挂载时立刻
//   `fetchCategories()`，`currentSpaceId` 为空时那方法**直接返回**（不报错）。
//   所以「装完再挂」这件事错了，界面照样打得开，只是分类下拉是空的。
//
// （整板看板原来也在这里 —— 它是同一个「把老页面套进新外壳」的形状。第七批之后它
// 换成了这块板自己的页面（`Analytics.vue`），底下不再是老页面，接缝也就没有可测的
// 东西了；那一页自己由 `Analytics.spec.ts` 测。）
//
// 所以每一条都让替身替掉底下那一页，**替身自己把接缝上的东西读出来渲染到 DOM**——
// 断言的是「挂进去之后实际拿到什么」，不是「源码里写了什么」。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BOARD_TASK_ROUTE_NAMES } from '../routeNames'

import TaskDetail from './TaskDetail.vue'
import TaskPublish from './TaskPublish.vue'

import { OLD_TASK_ROUTE_NAMES, useTaskRouteNames } from '@/lib/shellRouteNames'

const spaceDetail = vi.fn()
const listCategories = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 页头那一层：真组件走的是面包屑（只有老树的路由 meta 拼得出来），这里只留它的
// 默认插槽，插槽里那颗「回到题目板」是真的 —— 断言的是它的 `to` 落成哪个地址。
vi.mock('@/components/common/PageHeader.vue', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  return {
    default: dc({
      name: 'PageHeaderStub',
      setup(_, { slots }) {
        // 看板那层把六格 Tab 放在 `#tabs` 里，详情那层把「回到题目板」放在默认插槽。
        return () => hh('div', { 'data-testid': 'page-header' }, [slots.default?.(), slots.tabs?.()])
      },
    }),
  }
})

// 详情底下那一页：替身把它从 provide 里读到的路由名挂在 DOM 上。
vi.mock('@/views/tasks/Detail.vue', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  const { useTaskRouteNames: read } = await import('@/lib/shellRouteNames')
  return {
    default: dc({
      name: 'TaskDetailProbe',
      setup() {
        const names = read()
        return () =>
          hh('div', { 'data-testid': 'task-detail-probe' }, [
            ...Object.entries(names).map(([key, value]) =>
              hh('span', { 'data-testid': `task-name-${key}` }, String(value))
            ),
          ])
      },
    }),
  }
})

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

/** 只有被测的那三层要落上去的那几条路由：这一份量的不是「地址接没接住」
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

/** 从挂载结果里读一个 testid 的文字 —— 断言都走这几只小手，免得满篇 querySelector。
 *  收 `Element` 而不是 `HTMLElement`：testing-library 给的容器就是前者。 */
function textOf(container: Element, testId: string): string | null {
  return container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? null
}

function nameOf(container: Element, key: string): string | null {
  return textOf(container, `task-name-${key}`)
}

describe('详情、发题挂进新外壳', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    spaceDetail.mockImplementation(async () => ({ data: { space: { id: SPACE_ID, name: '数据结构空间' } } }))
    listCategories.mockImplementation(async () => ({ data: { categories: [{ id: 3, name: '基础题' }] } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('详情：底下那一页拿到的是新树那一套路由名', async () => {
    const { container } = await mountAt(`/spaces/${SPACE_ID}/board/tasks/42`, TaskDetail)
    expect(nameOf(container, 'detail')).toBe(BOARD_TASK_ROUTE_NAMES.detail)
    expect(nameOf(container, 'submissions')).toBe(BOARD_TASK_ROUTE_NAMES.submissions)
    expect(nameOf(container, 'spaceHome')).toBe(BOARD_TASK_ROUTE_NAMES.spaceHome)
  })

  it('详情：没有新外壳包着时仍然是老树那一套 —— 老页面一个字没改', async () => {
    // 不 provide 就是老树的样子：新外壳那一层是唯一的差异来源。
    const Bare = defineComponent({
      setup() {
        const names = useTaskRouteNames()
        return () => h('div', { 'data-testid': 'bare' }, names.submissions)
      },
    })
    const { container } = await mountAt(`/spaces/${SPACE_ID}/board/tasks/42`, Bare)
    expect(container.querySelector('[data-testid="bare"]')?.textContent).toBe(OLD_TASK_ROUTE_NAMES.submissions)
  })

  it('详情：页头那颗按钮回到题目板首页，而且带着空间 id', async () => {
    const { container } = await mountAt(`/spaces/${SPACE_ID}/board/tasks/42`, TaskDetail)
    const link = container.querySelector('a')
    expect(link?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/board`)
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
