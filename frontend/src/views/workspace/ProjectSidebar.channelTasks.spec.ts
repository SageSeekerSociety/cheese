/**
 * 侧栏挂在频道下面的任务，来自频道清单的那一行（`my_tasks`）：和我有关的几件，加这个
 * 频道进行中的总数。整个项目的任务后端每读一次都要全部算一遍（dev 上一个项目 1400 多
 * 条），侧栏不为这几行去读它。任务变了，房间推来一帧，重读的是那个频道那一行。
 *
 * 用的是真的项目框路由、真的 ProjectSidebar、真的 store 和缓存；「任务变了」走的是房间
 * 推送进来的那条路（`query/changes`）。只把接口换成替身。
 */
import type { Topic } from '@/cx_types'

import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VApp } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
      // 每个频道下面挂着几件、一共几件：侧栏递给列表的就是这两份。
      setup:
        (_props, { attrs }) =>
        () =>
          h('nav', {
            'data-testid': 'topic-list',
            'data-tasks': JSON.stringify(attrs['room-tasks'] ?? {}),
            'data-totals': JSON.stringify(attrs['room-task-totals'] ?? {}),
          }),
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
vi.mock('@/lib/routePrefetch', () => ({ prefetchOnHover: vi.fn(), cancelPrefetch: vi.fn(), prefetchNow: vi.fn() }))

const api = vi.hoisted(() => ({ listProjectTasks: vi.fn(), getTopic: vi.fn() }))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listProjectTasks: api.listProjectTasks,
  getTopic: api.getTopic,
}))

import { roomChanged } from '@/query/changes'
import { workspaceRoutes } from '@/router/workspaceRoutes'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

const task = (id: string) => ({
  id,
  room_id: 'topic-a',
  title: id,
  status: 'open',
  presentation: { column: 'building' },
})

function channel(id: string, shown: string[], open: number): Topic {
  return {
    id,
    project_id: 'p',
    title: id,
    kind: 'channel',
    status: 'active',
    joined: true,
    my_tasks: { shown: shown.map(task), open },
  } as unknown as Topic
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function openProject(topics: Topic[]) {
  seedProjects([])
  seedProject('p', { topics, members: [], notifyLevels: {} })
  useWorkspaceStore().openProject('p')
  display.width = ref(1280)
  display.mdAndUp = ref(true)
  const router = createRouter({ history: createMemoryHistory(), routes: [workspaceRoutes] })
  const Root = defineComponent({
    setup: () => () => h(VApp, null, () => [h(RouterView, { name: 'sidebar' }), h(RouterView)]),
  })
  render(Root, { global: { plugins: [router, createVuetify({ components, directives })] } })
  await router.push('/projects/p/topics/topic-a')
  await screen.findByTestId('topic-list')
  await settle()
  return router
}

const list = () => screen.getByTestId('topic-list').dataset

beforeEach(() => {
  setActivePinia(createPinia())
  api.listProjectTasks.mockReset()
  api.getTopic.mockReset()
})

describe('侧栏挂在频道下面的任务', () => {
  it('就是频道那一行带着的：和我有关的几件，和一共几件', async () => {
    await openProject([channel('topic-a', ['k1', 'k2'], 5), channel('topic-b', [], 0)])

    expect(JSON.parse(list().tasks!)).toEqual({ 'topic-a': [task('k1'), task('k2')] })
    expect(JSON.parse(list().totals!)).toEqual({ 'topic-a': 5 })
  })

  it('不为这几行读整个项目的任务，换频道也不读', async () => {
    const router = await openProject([channel('topic-a', ['k1'], 1), channel('topic-b', [], 0)])
    for (const at of ['topic-b', 'topic-a']) {
      await router.push(`/projects/p/topics/${at}`)
      await settle()
    }

    expect(api.listProjectTasks).not.toHaveBeenCalled()
  })

  it('任务变了：那个频道那一行重读，侧栏跟着变', async () => {
    await openProject([channel('topic-a', ['k1'], 1), channel('topic-b', [], 0)])
    api.getTopic.mockResolvedValue(channel('topic-a', ['k1', 'k3'], 2))

    await roomChanged({ room: 'topic-a', project: 'p', resource: 'tasks' })
    await settle()

    expect(api.getTopic).toHaveBeenCalledWith('topic-a')
    expect(JSON.parse(list().totals!)).toEqual({ 'topic-a': 2 })
    expect(api.listProjectTasks).not.toHaveBeenCalled()
  })
})
