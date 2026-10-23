/**
 * 频道名前的任务折叠箭头：点一下，把挂在它下面的一整块任务收起来。
 *
 * 收起来的是画法，不是数据：频道行不显示任务数量，折叠任务的灯与频道自身的灯合成一盏。
 * 整块放下，里面一条都不留：正开着的那条任务也一起走，你回到这个频道的页面。
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

import { t } from '@/i18n'
import { rememberNumbered, rememberProject } from '@/lib/addresses'
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

function channel(
  id: string,
  shown: ReturnType<typeof task>[],
  open: number,
  over: Record<string, unknown> = {}
): Topic {
  return {
    id,
    project_id: 'p',
    title: id,
    kind: 'channel',
    status: 'active',
    joined: true,
    my_tasks: { shown, open },
    ...over,
  } as unknown as Topic
}

/** 任务页的地址里只有任务，「它在哪个频道」靠地址表补上（`routeIds`）。真实运行里是任务
 *  清单读回来时记下的，这里没有那一步，就自己记一份。 */
function rememberTask(id: string, roomId: string, number: number) {
  rememberProject('p', 'p')
  rememberNumbered('tasks', [{ id, project_id: 'p', room_id: roomId, number }])
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
/** 某个频道那一行上的那颗把手：侧栏上有好几个频道时，按行取（行上带着房间 id）。 */
function foldIn(roomId: string): HTMLElement {
  return document.querySelector(`[data-room-id="${roomId}"] .task-fold__btn`) as HTMLElement
}
/** 正文停在哪一页、哪个频道/任务上——收起带走的是这个，不是地址栏怎么写。 */
function here(router: { currentRoute: { value: { name?: unknown; params: Record<string, unknown> } } }) {
  return { name: router.currentRoute.value.name, ...router.currentRoute.value.params }
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.listProjectTasks.mockReset()
  // 蓝灯认的是「这条活归我」，所以得有个登录的人。
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }))
})

describe('频道下面那一整块任务，收得起来', () => {
  it('收起来：任务行走光但不显示数量；再点一下回来', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一'), task(K2, '任务二')], 5)])
    expect(screen.getByText('任务二')).toBeTruthy()
    expect(fold().getAttribute('aria-expanded')).toBe('true')

    await fireEvent.click(fold())
    await settle()

    expect(screen.queryByText('任务一')).toBeNull()
    expect(screen.queryByText('任务二')).toBeNull()
    expect(unfold().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByTitle('5 tasks under way')).toBeNull()
    const row = document.querySelector('[data-room-id="topic-a"]') as HTMLElement
    expect(row.textContent?.trim()).toBe('topic-a')

    await fireEvent.click(unfold())
    await settle()

    expect(screen.getByText('任务一')).toBeTruthy()
    expect(screen.getByText('任务二')).toBeTruthy()
    expect(screen.queryByTitle('5 tasks under way')).toBeNull()
  })

  it('任务箭头在频道名前，点击只折叠任务、不打开频道', async () => {
    const router = await openProject([
      channel('topic-a', [task(K1, '任务一')], 5),
      channel('topic-b', [task(K2, '任务二', 'topic-b')], 1),
    ])
    rememberTask(K2, 'topic-b', 2)
    await router.push(`/projects/p/tasks/${K2}`)
    await settle()
    const row = document.querySelector('[data-room-id="topic-a"]') as HTMLElement
    const title = row.querySelector('[data-user-content="topic-a"]') as HTMLElement
    const arrow = foldIn('topic-a')
    expect(arrow.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    await fireEvent.click(arrow)
    await settle()

    expect(here(router)).toEqual({ name: 'workspace-task', projectId: 'p', taskId: K2 })
    expect(screen.queryByText('任务一')).toBeNull()
    expect(screen.getByText('任务二')).toBeTruthy()
  })

  it('不显示「展开其余 N 个」，也不为这一行去读整个项目的任务', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一'), task(K2, '任务二')], 5)])
    expect(screen.queryByRole('button', { name: /Show 3 more/ })).toBeNull()

    await fireEvent.click(fold())
    await settle()

    expect(screen.queryByRole('button', { name: /Show 3 more/ })).toBeNull()
    expect(api.listProjectTasks).not.toHaveBeenCalled()
  })

  it('正在这条任务里时收起：回到频道页，那条任务不再留在下面', async () => {
    const router = await openProject([channel('topic-a', [task(K1, '任务一'), task(K2, '任务二')], 5)])
    rememberTask(K1, 'topic-a', 1)
    await router.push(`/projects/p/tasks/${K1}`)
    await settle()
    // 任务页的地址里只有任务，它所在的频道由地址表补上（`hereChannelId`）。
    expect(here(router)).toEqual({ name: 'workspace-task', projectId: 'p', taskId: K1 })

    await fireEvent.click(fold())
    await settle()

    // 正文换成这个频道的页面（地址栏里的短名不一定是它原来那个写法）。
    expect(here(router)).toEqual({ name: 'workspace-topic', projectId: 'p', topicId: 'topic-a' })
    expect(screen.queryByText('任务一')).toBeNull()
    expect(screen.queryByText('任务二')).toBeNull()
  })

  it('收起只发生在你正看着的这个频道里：正文在别处时不动', async () => {
    const router = await openProject([
      channel('topic-a', [task(K1, '任务一')], 1),
      channel('topic-b', [task(K2, '任务二', 'topic-b')], 1),
    ])
    await router.push(`/projects/p/tasks/${K2}`)
    await settle()

    // topic-a 那一行的把手：正开着的那条在 topic-b 里——收起它不该把人带走。
    await fireEvent.click(foldIn('topic-a'))
    await settle()

    expect(here(router)).toEqual({ name: 'workspace-task', projectId: 'p', taskId: K2 })
    expect(screen.getByText('任务二')).toBeTruthy()
  })

  it('再展开也不把人带回那条任务：正文留在这个频道页上', async () => {
    const router = await openProject([channel('topic-a', [task(K1, '任务一')], 1)])
    rememberTask(K1, 'topic-a', 1)
    await router.push(`/projects/p/tasks/${K1}`)
    await settle()
    await fireEvent.click(fold())
    await settle()

    await fireEvent.click(unfold())
    await settle()

    expect(here(router).name).toBe('workspace-topic')
    expect(screen.getByText('任务一')).toBeTruthy()
  })

  it.each([
    { room: { awaits_me: true }, task: { moving: true }, label: 'yellow' },
    { room: {}, task: { awaits_me: true }, label: 'yellow' },
    { room: {}, task: { moving: true }, label: 'moving' },
  ])('频道与收起任务合成一盏灯：$label', async ({ room, task: facts, label }) => {
    await openProject([channel('topic-a', [task(K1, '任务一', 'topic-a', facts)], 3, room)])

    await fireEvent.click(fold())
    await settle()

    const row = document.querySelector('[data-room-id="topic-a"]') as HTMLElement
    const lights = row.querySelectorAll('[role="img"]')
    expect(lights).toHaveLength(1)
    expect(lights[0]!.getAttribute('aria-label')).toBe(t(`work.sidebar.light.${label}`))
    expect(row.textContent?.trim()).toBe('topic-a')

    await fireEvent.click(unfold())
    await settle()

    // 展开之后不再用任务替频道说话：任务的状态回到任务行上。
    expect(row.querySelectorAll('.row-light')).toHaveLength(room.awaits_me ? 1 : 0)
    // 任务行自己只在有事等我时亮灯；在跑的那一条靠转动的「在做」图标，不再呼吸。
    expect(screen.getByRole('button', { name: /任务一/ }).querySelectorAll('.row-light')).toHaveLength(
      facts.awaits_me ? 1 : 0
    )
  })

  it('没有任务挂着的频道没有这个把手', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一')], 1), channel('topic-b', [], 0)])
    await settle()

    expect(screen.getAllByRole('button', { name: "Collapse this channel's tasks" })).toHaveLength(1)
  })

  it('只显示和我有关的任务，过滤掉 filling_in 凑数任务，任务行不画蓝灯、报错红灯或呼吸绿', async () => {
    await openProject([
      channel(
        'topic-a',
        [
          task(K1, '任务一', 'topic-a', { owner_handle: 'me' }),
          task(K2, '任务二', 'topic-a', { owner_handle: 'wang', filling_in: true, unseen_delivery: true }),
          task(K3, '任务三', 'topic-a', { moving: true }),
        ],
        4
      ),
    ])

    const mine = screen.getByRole('button', { name: /任务一/ })
    expect(mine.querySelectorAll('.row-light')).toHaveLength(0)

    // 在跑的任务行不画呼吸绿：同一行只留转动的「在做」图标在动。
    expect(screen.getByRole('button', { name: /任务三/ }).querySelectorAll('.row-light')).toHaveLength(0)

    expect(screen.queryByRole('button', { name: /任务二/ })).toBeNull()
  })
})
