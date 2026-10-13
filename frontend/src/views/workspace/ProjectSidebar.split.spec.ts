/**
 * 平板宽度（768–959）上话题列表和房间并排：在列表里点一个话题，换的只是右边的房间，
 * 左边的列表留在原处——不重新挂载（滚动位置、展开的分组都还在），也不被一整页盖掉。
 * 手机上（< 768）房间仍是一整页，列表不和它同屏。
 *
 * 用的是真的项目框路由（workspaceRoutes）和真的 ProjectSidebar；房间、框架层和列表
 * 本身换成替身，只数列表被挂了几次、点的是哪个话题。
 */
import { defineComponent, h, reactive, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VApp } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const display = vi.hoisted(() => ({
  width: null as unknown as { value: number },
  mdAndUp: null as unknown as { value: boolean },
}))
vi.mock('vuetify', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vuetify')>()),
  useDisplay: () => display,
}))

const mounts = vi.hoisted(() => ({ count: 0 }))
vi.mock('@/components/TopicSidebar.vue', async () => {
  const { defineComponent, h, onMounted } = await import('vue')
  return {
    default: defineComponent({
      props: { topics: { type: Array, default: () => [] } },
      emits: ['select-topic'],
      setup(props, { emit }) {
        onMounted(() => (mounts.count += 1))
        return () =>
          h(
            'nav',
            { 'data-testid': 'topic-list' },
            (props.topics as { id: string; title: string }[]).map((t) =>
              h('button', { onClick: () => emit('select-topic', t.id) }, t.title)
            )
          )
      },
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
  return { default: defineComponent({ setup: () => () => h('div', { 'data-testid': 'no-room' }) }) }
})
vi.mock('@/views/workspace/TopicView.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      props: { topicId: { type: String, required: true } },
      setup: (props) => () => h('section', { 'data-testid': 'room' }, props.topicId),
    }),
  }
})
vi.mock('@/views/workspace/ProjectOverview.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({ setup: () => () => h('div', { 'data-testid': 'overview' }) }) }
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
})
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))
// 侧栏挂在房间下的任务另读一份；这里钉的不是它。
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  listProjectTasks: async () => ({ data: [], total: 0 }),
}))

import { workspaceRoutes } from '@/router/workspaceRoutes'

async function openProjectAt(width: number) {
  display.width = ref(width)
  display.mdAndUp = ref(width >= 960)
  const router = createRouter({ history: createMemoryHistory(), routes: [workspaceRoutes] })
  const Root = defineComponent({
    setup: () => () => h(VApp, null, () => [h(RouterView, { name: 'sidebar' }), h(RouterView)]),
  })
  render(Root, { global: { plugins: [router, createVuetify({ components, directives })] } })
  await router.push('/projects/p1')
  return router
}

beforeEach(() => {
  mounts.count = 0
})

describe('平板上的两栏', () => {
  it('在列表里换话题只换右边的房间，列表不重新挂载', async () => {
    const router = await openProjectAt(820)
    await screen.findByTestId('topic-list')
    expect(mounts.count).toBe(1)

    await fireEvent.click(screen.getByText('Room A'))
    await waitFor(() => expect(screen.getByTestId('room').textContent).toBe('topic-a'))
    expect(router.currentRoute.value.params.topicId).toBe('topic-a')

    await fireEvent.click(screen.getByText('Room B'))
    await waitFor(() => expect(screen.getByTestId('room').textContent).toBe('topic-b'))
    expect(router.currentRoute.value.params.topicId).toBe('topic-b')

    expect(screen.getByTestId('topic-list')).toBeTruthy()
    expect(mounts.count).toBe(1)
  })

  it('项目的其余各页在平板上仍是一整页', async () => {
    const router = await openProjectAt(820)
    await screen.findByTestId('topic-list')
    await router.push('/projects/p1/overview').catch(() => {})
    await waitFor(() => expect(screen.queryByTestId('topic-list')).toBeNull())
  })

  it('手机上房间是一整页，列表不和它同屏', async () => {
    const router = await openProjectAt(390)
    await router.push('/projects/p1/topics/topic-a')
    await screen.findByTestId('room')
    expect(screen.queryByTestId('topic-list')).toBeNull()
  })
})
