/**
 * 频道下面任务那几行的最后一行：侧栏先只挂和我有关的几件，这一行就地把频道里其余进行中
 * 的任务展开在下面，再点一下收回去。它不换页；去「全部任务」那一页走频道的 ⋯。
 *
 * 用的是真的项目框路由、真的 ProjectSidebar 和 TopicSidebar、真的 store 和缓存，只把
 * 接口换成替身。
 */
import type { Topic } from '@/cx_types'

import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VApp } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
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

const api = vi.hoisted(() => ({ listProjectTasks: vi.fn() }))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listProjectTasks: api.listProjectTasks,
}))

import { topicActions } from '@/commands/topicActions'
import { workspaceRoutes } from '@/router/workspaceRoutes'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

function task(id: string, room = 'topic-a', over: Record<string, unknown> = {}) {
  return {
    id,
    room_id: room,
    title: `任务${id}`,
    title_source: 'person',
    status: 'open',
    created_at: '2026-10-10T00:00:00Z',
    presentation: { column: 'building', phrase: 'started' },
    ...over,
  }
}

function channel(id: string, shown: string[], open: number): Topic {
  return {
    id,
    project_id: 'p',
    title: id,
    kind: 'channel',
    status: 'active',
    joined: true,
    my_tasks: { shown: shown.map((t) => task(t, id)), open },
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
  await screen.findByText('任务k1')
  await settle()
  return router
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.listProjectTasks.mockReset()
  // 项目里还开着的任务：topic-a 里两件是我的（侧栏已经挂着），三件是别人的；另有一件
  // 已经合并、正在收尾，一件在别的频道。
  api.listProjectTasks.mockResolvedValue({
    data: [
      task('k1'),
      task('k2'),
      task('k3', 'topic-a', { created_at: '2026-10-09T00:00:00Z' }),
      task('k4', 'topic-a', { created_at: '2026-10-11T00:00:00Z' }),
      task('k5'),
      task('k8', 'topic-a', { presentation: { column: 'done', phrase: 'accepted' } }),
      task('k7', 'topic-b'),
    ],
  })
})

describe('频道下面「展开其余」那一行', () => {
  it('说清还有几个，展开就在原地列出频道里其余进行中的任务，不换页', async () => {
    const router = await openProject([channel('topic-a', ['k1', 'k2'], 5), channel('topic-b', [], 1)])
    const before = router.currentRoute.value.fullPath
    const more = screen.getByRole('button', { name: /Show 3 more/ })
    expect(screen.queryByText('任务k3')).toBeNull()

    await fireEvent.click(more)
    await settle()

    for (const id of ['k1', 'k2', 'k3', 'k4', 'k5']) expect(screen.getByText(`任务${id}`)).toBeTruthy()
    // 收尾中的不算进行中；别的频道的挂在那个频道自己下面，不在这里。
    expect(screen.queryByText('任务k8')).toBeNull()
    expect(screen.queryByText('任务k7')).toBeNull()
    expect(router.currentRoute.value.fullPath).toBe(before)
    expect(screen.getByRole('button', { name: /Show fewer/ }).getAttribute('aria-expanded')).toBe('true')
  })

  it('收起后回到只挂我那几件', async () => {
    await openProject([channel('topic-a', ['k1', 'k2'], 5)])
    await fireEvent.click(screen.getByRole('button', { name: /Show 3 more/ }))
    await settle()

    await fireEvent.click(screen.getByRole('button', { name: /Show fewer/ }))
    await settle()

    expect(screen.getByText('任务k1')).toBeTruthy()
    expect(screen.queryByText('任务k3')).toBeNull()
    expect(screen.getByRole('button', { name: /Show 3 more/ })).toBeTruthy()
  })

  it('频道里进行中的都已经挂着，就没有这一行', async () => {
    await openProject([channel('topic-a', ['k1', 'k2'], 2)])

    // 没有藏着的，最后不再多一行（展开的、换页的都没有）。
    expect(screen.queryByRole('button', { name: /Show \d+ more|All tasks/ })).toBeNull()
    expect(api.listProjectTasks).not.toHaveBeenCalled()
  })

  it('读失败了说一声，再点一下重读', async () => {
    api.listProjectTasks.mockRejectedValueOnce(new Error('boom'))
    await openProject([channel('topic-a', ['k1', 'k2'], 5)])
    await fireEvent.click(screen.getByRole('button', { name: /Show 3 more/ }))
    await settle()

    await fireEvent.click(await screen.findByRole('button', { name: /Couldn't load/ }))
    await settle()

    expect(screen.getByText('任务k3')).toBeTruthy()
  })
})

describe('频道的 ⋯ 里的「全部任务」', () => {
  it('打开这个频道的全部任务那一页', async () => {
    const router = await openProject([channel('topic-a', ['k1'], 1)])
    const topic = useWorkspaceStore().topics.find((row) => row.id === 'topic-a')!

    topicActions(topic, router, { rename: () => {} }).find((action) => action.id === 'topic.allTasks')!.run!()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('project-tasks'))
    expect(router.currentRoute.value.query.channel).toBe('topic-a')
  })
})
