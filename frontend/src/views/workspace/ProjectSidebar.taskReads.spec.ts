/**
 * 侧栏挂在频道下面的任务，读的是整个项目的任务清单。后端每读一次都要把全项目的任务
 * 算一遍（dev 上一个项目 1400 多条），所以它只在有理由的时候读：任务变了，或者手上那份
 * 已经旧了。在频道之间切来切去不是理由；看不见的标签页也不该一直读。它只挂还在进行的
 * 任务，也就只要这些：已经关掉的是它们的十几倍。
 *
 * 用的是真的项目框路由、真的 ProjectSidebar 和真的 `readProjectTasks`；只把接口换成
 * 计数的替身。
 */
import { defineComponent, h, reactive, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VApp } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const display = vi.hoisted(() => ({
  width: null as unknown as { value: number },
  mdAndUp: null as unknown as { value: boolean },
}))
vi.mock('vuetify', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vuetify')>()),
  useDisplay: () => display,
}))

vi.mock('@/components/TopicSidebar.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      inheritAttrs: false,
      // 每个频道下面挂着几件还在进行的任务：侧栏用的就是这一份。
      setup:
        (_props, { attrs }) =>
        () =>
          h('nav', { 'data-testid': 'topic-list', 'data-totals': JSON.stringify(attrs['room-task-totals'] ?? {}) }),
    }),
  }
})
vi.mock('@/views/workspace/ProjectShell.vue', async () => {
  const { RouterView } = await import('vue-router')
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({ setup: () => () => h(RouterView) }) }
})
vi.mock('@/views/workspace/WorkspaceEntry.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({ setup: () => () => h('div') }) }
})
vi.mock('@/views/workspace/TopicView.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({ setup: () => () => h('section') }) }
})
vi.mock('@/lib/routePrefetch', () => ({ prefetchOnHover: vi.fn(), cancelPrefetch: vi.fn() }))

const store = reactive({
  accessDenied: null,
  projects: [],
  projectId: 'p1',
  topics: [
    { id: 'topic-a', title: 'Room A' },
    { id: 'topic-b', title: 'Room B' },
  ],
  loadingTopics: false,
  unreadMap: {},
  privateUnreadMap: {},
  tasksChanged: 0,
})
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

const reads = vi.hoisted(() => ({
  count: 0,
  closedToo: false,
  // 为真时读不马上回来，答复排在 `answers` 里由测试决定先后。
  hold: false,
  answers: [] as Array<(rows: unknown[]) => void>,
}))
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  listProjectTasks: async (_projectId: string, opts: { open?: boolean } = {}) => {
    reads.count += 1
    if (!opts.open) reads.closedToo = true
    if (reads.hold)
      return new Promise((resolve) => reads.answers.push((rows) => resolve({ data: rows, total: rows.length })))
    return { data: [], total: 0 }
  },
}))

import { workspaceRoutes } from '@/router/workspaceRoutes'

let visibility: DocumentVisibilityState = 'visible'

async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve()
}

async function openProject(projectId: string) {
  display.width = ref(1280)
  display.mdAndUp = ref(true)
  const router = createRouter({ history: createMemoryHistory(), routes: [workspaceRoutes] })
  const Root = defineComponent({
    setup: () => () => h(VApp, null, () => [h(RouterView, { name: 'sidebar' }), h(RouterView)]),
  })
  render(Root, { global: { plugins: [router, createVuetify({ components, directives })] } })
  await router.push(`/projects/${projectId}/topics/topic-a`)
  await screen.findByTestId('topic-list')
  await settle()
  return router
}

beforeEach(() => {
  reads.count = 0
  reads.closedToo = false
  reads.hold = false
  reads.answers = []
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('侧栏的任务清单什么时候重读', () => {
  it('只要还在进行的任务', async () => {
    await openProject('p-open')
    expect(reads.count).toBe(1)
    expect(reads.closedToo).toBe(false)
  })

  it('在频道之间切换不重读', async () => {
    const router = await openProject('p-switch')
    expect(reads.count).toBe(1)

    for (const at of ['topic-b', 'topic-a', 'topic-b']) {
      vi.advanceTimersByTime(5_000)
      await router.push(`/projects/p-switch/topics/${at}`)
      await settle()
    }

    expect(reads.count).toBe(1)
  })

  it('看不见的标签页不读，回到前台时补一次', async () => {
    await openProject('p-hidden')
    expect(reads.count).toBe(1)

    visibility = 'hidden'
    document.dispatchEvent(new Event('visibilitychange'))
    vi.advanceTimersByTime(5 * 60_000)
    await settle()
    expect(reads.count).toBe(1)

    visibility = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
    await settle()
    expect(reads.count).toBe(2)
  })

  it('任务变了就重读，哪怕上一次读还没回来；晚回来的那一份不盖掉新的', async () => {
    await openProject('p-changed')
    reads.hold = true
    // 一次例行的读发出去了，还没回来；它带回来的是任务变之前的样子。
    vi.advanceTimersByTime(31_000)
    await settle()
    expect(reads.count).toBe(2)

    store.tasksChanged += 1
    await settle()
    expect(reads.count).toBe(3)

    const open = { id: 'k1', room_id: 'topic-a', status: 'open', presentation: { column: 'building' } }
    const [before, after] = reads.answers
    after([])
    await settle()
    before([open])
    await settle()

    expect(screen.getByTestId('topic-list').dataset.totals).toBe('{}')
  })

  it('同一次改动连着说了几遍，只重读一次', async () => {
    await openProject('p-burst')
    reads.hold = true
    store.tasksChanged += 1
    store.tasksChanged += 1
    await settle()
    store.tasksChanged += 1
    await settle()

    expect(reads.count).toBe(2)
  })
})
