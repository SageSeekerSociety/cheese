/**
 * 频道行右边那个折叠加手：点一下，把挂在它下面的一整块任务收起来。
 *
 * 收起来的是画法，不是数据——这个频道里进行中的条数和那盏灯挪到频道行上（不然收起来
 * 就成了一条没有信息量的空白），你正打开的那条任务留在下面（收起不能把你手里的事一起
 * 收掉）。
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

import { workspaceRoutes } from '@/router/workspaceRoutes'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

// 地址里任务可以写编号或 UUID（`NUMBER_OR_ID`），'k1' 那样两样都不是，路由匹配不上。
const K1 = '5c2d1e0f-1f22-4a33-9c44-000000000001'
const K2 = '5c2d1e0f-1f22-4a33-9c44-000000000002'

function task(id: string, title: string, room = 'topic-a', over: Record<string, unknown> = {}) {
  return {
    id,
    room_id: room,
    title,
    title_source: 'person',
    status: 'open',
    created_at: '2026-10-10T00:00:00Z',
    presentation: { column: 'building', phrase: 'started' },
    ...over,
  }
}

function channel(id: string, shown: ReturnType<typeof task>[], open: number): Topic {
  return {
    id,
    project_id: 'p',
    title: id,
    kind: 'channel',
    status: 'active',
    joined: true,
    my_tasks: { shown, open },
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
  await screen.findByText('任务一')
  await settle()
  return router
}

const fold = () => screen.getByRole('button', { name: "Collapse this channel's tasks" })
const unfold = () => screen.getByRole('button', { name: "Expand this channel's tasks" })

beforeEach(() => {
  setActivePinia(createPinia())
  api.listProjectTasks.mockReset()
})

describe('频道下面那一整块任务，收得起来', () => {
  it('收起来：任务行走光，频道行上留下进行中的条数；再点一下回来', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一'), task(K2, '任务二')], 5)])
    expect(screen.getByText('任务二')).toBeTruthy()

    await fireEvent.click(fold())
    await settle()

    expect(screen.queryByText('任务一')).toBeNull()
    expect(screen.queryByText('任务二')).toBeNull()
    expect(screen.getByTitle('5 tasks under way').textContent).toBe('5')

    await fireEvent.click(unfold())
    await settle()

    expect(screen.getByText('任务一')).toBeTruthy()
    expect(screen.getByText('任务二')).toBeTruthy()
    expect(screen.queryByTitle('5 tasks under way')).toBeNull()
  })

  it('收起时频道行挂上那一档：行尾变宽了，⋯ 要让开', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一')], 5)])
    const row = () => document.querySelector('.topic-row--tasks') as HTMLElement

    expect(row().classList.contains('topic-row--tasks-collapsed')).toBe(false)

    await fireEvent.click(fold())
    await settle()

    // 收起之后行尾多出灯和条数，把手比展开时宽了 35px；⋯ 是浮在行尾的一层，
    // 让多少写在这条 class 对应的 CSS 里。这一层量不出排版，只能钉住挂在
    // 哪一档上——档位掉了，⋯ 就会重新压在那颗条数上（2026-10-13 量到的 21px）。
    expect(row().classList.contains('topic-row--tasks-collapsed')).toBe(true)
  })

  it('「展开其余 N 个」那一行跟着那一块一起收起来', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一'), task(K2, '任务二')], 5)])
    expect(screen.getByRole('button', { name: /Show 3 more/ })).toBeTruthy()

    await fireEvent.click(fold())
    await settle()

    expect(screen.queryByRole('button', { name: /Show 3 more/ })).toBeNull()
    // 收起的是画法：没有为这一行去读整个项目的任务。
    expect(api.listProjectTasks).not.toHaveBeenCalled()
  })

  it('收起时留住你正打开的那一条', async () => {
    const router = await openProject([channel('topic-a', [task(K1, '任务一'), task(K2, '任务二')], 5)])
    await router.push(`/projects/p/tasks/${K1}`)
    await settle()

    await fireEvent.click(fold())
    await settle()

    expect(screen.getByText('任务一')).toBeTruthy()
    expect(screen.queryByText('任务二')).toBeNull()
  })

  it('没有任务挂着的频道没有这个把手', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一')], 1), channel('topic-b', [], 0)])
    await settle()

    expect(screen.getAllByRole('button', { name: "Collapse this channel's tasks" })).toHaveLength(1)
  })

  it('凑够三条借来的那几条，行上说是补上来的', async () => {
    await openProject([
      channel('topic-a', [task(K1, '任务一'), task(K2, '任务二', 'topic-a', { filling_in: true })], 4),
    ])

    const chip = screen.getByText('Filled in')
    // 补的那一条明确说是补的：不然会像一条你漏看了的活。
    expect(chip.getAttribute('title')).toBe('Fewer than three of your own tasks are here; this row makes up the three')
    // 和我有关的那些不挂这个签。
    expect(screen.getAllByText('Filled in')).toHaveLength(1)
  })
})
