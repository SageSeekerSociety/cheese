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
import type { ProjectMemberRow, Topic } from '@/cx_types'

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
const K3 = '5c2d1e0f-1f22-4a33-9c44-000000000003'

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

async function openProject(topics: Topic[], members: ProjectMemberRow[] = []) {
  seedProjects([])
  // 登录的人有 handle，未读一并按它读：这里先摆好空的一份，别让它真的去请求（网络是封的）。
  seedProject('p', { topics, members, notifyLevels: {}, unread: {}, privateUnread: {} })
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
  // 蓝灯认的是「这条不是我负责的」，所以得有个登录的人。
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }))
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

  it('别人的活画一盏蓝灯、盖过状态灯；我负责的和没主的都不画', async () => {
    await openProject([
      channel(
        'topic-a',
        [
          task(K1, '任务一', 'topic-a', { owner_handle: 'me' }),
          // 这条在往前跑：它该画呼吸绿，但「不归我」压过「在跑」。
          task(K2, '任务二', 'topic-a', { owner_handle: 'wang', filling_in: true, moving: true }),
          task(K3, '任务三', 'topic-a'),
        ],
        4
      ),
    ])

    // 别人的那一条只有一盏灯，是蓝的：灯是给读屏用的图，名字就是它要说的事。
    const theirs = screen.getByRole('button', { name: /任务二/ })
    const lights = theirs.querySelectorAll('.row-light')
    expect(lights).toHaveLength(1)
    expect(lights[0]!.classList.contains('row-light--blue')).toBe(true)
    // 灯是给读屏看的图，蓝灯又把状态灯盖掉了：它必须自己说得出这一行归谁。
    expect(lights[0]!.getAttribute('aria-label')).toBeTruthy()

    // 借来的那一条还靠悬浮说明说出它为什么在这儿：这个频道里我自己的不到三条。
    // 断它必须说出的那件事，不整句比——整句比较只会在改文案时红一次
    // （王长鑫 2026-10-12 审阅 #3498 提的，`.claude/rules` 之外的那条约定）。
    expect(theirs.getAttribute('title')).toContain('Fewer than three')

    // 我负责的那一条、还有没主的那一条，都只按状态画灯；没有状态就不画灯。
    expect(screen.getByRole('button', { name: /任务一/ }).querySelector('.row-light')).toBeNull()
    expect(screen.getByRole('button', { name: /任务三/ }).querySelector('.row-light')).toBeNull()
  })
})
