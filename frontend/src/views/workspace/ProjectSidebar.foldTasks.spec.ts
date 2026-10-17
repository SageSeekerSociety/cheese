/**
 * 频道行右边那个折叠加手：点一下，把挂在它下面的一整块任务收起来。
 *
 * 收起来的是画法，不是数据——这个频道里进行中的条数和那盏灯挪到频道行上（不然收起来
 * 就成了一条没有信息量的空白）。整块放下，里面一条都不留：正开着的那条任务也一起走，
 * 你回到这个频道的页面。
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

  it('收起后那一块的灯只说里面的任务，不重复频道自己那盏', async () => {
    // 这个频道自己在等我（黄灯）；挂在它下面那一条没什么事。
    await openProject([channel('topic-a', [task(K1, '任务一')], 3, { awaits_me: true })])

    await fireEvent.click(fold())
    await settle()

    const rollup = document.querySelector('.task-fold__rollup') as HTMLElement
    // 频道行左边那盏已经说了「有事等你」；这一块里没有一条任务有事，就不该再画一颗黄的。
    // 剩下这一颗是蓝的：里面那条是我的活，蓝灯说的是「这条归我」，和频道那盏不是一件事。
    const lights = rollup.querySelectorAll('.row-light')
    expect(lights).toHaveLength(1)
    expect(lights[0]!.classList.contains('row-light--blue')).toBe(true)
    expect(rollup.textContent).toBe('3')
  })

  it('收起后那一块的灯：里面真有活等着，就亮它那一颗', async () => {
    await openProject([
      channel('topic-a', [task(K1, '任务一', 'topic-a', { awaits_me: true })], 3, { awaits_me: true }),
    ])

    await fireEvent.click(fold())
    await settle()

    const lights = (document.querySelector('.task-fold__rollup') as HTMLElement).querySelectorAll('.row-light')
    expect(lights).toHaveLength(1)
    expect(lights[0]!.classList.contains('row-light--yellow')).toBe(true)
  })

  it('没有任务挂着的频道没有这个把手', async () => {
    await openProject([channel('topic-a', [task(K1, '任务一')], 1), channel('topic-b', [], 0)])
    await settle()

    expect(screen.getAllByRole('button', { name: "Collapse this channel's tasks" })).toHaveLength(1)
  })

  it('我负责的和没主的画一盏蓝灯；别人的活只按状态画灯', async () => {
    await openProject([
      channel(
        'topic-a',
        [
          // 这条归我，状态上没别的话可说：画蓝灯。
          task(K1, '任务一', 'topic-a', { owner_handle: 'me' }),
          // 这条在往前跑，是别人负责的：只画它自己那盏呼吸绿，不画蓝。
          task(K2, '任务二', 'topic-a', { owner_handle: 'wang', filling_in: true, moving: true }),
          // 没主的算我这边的：也画蓝灯。
          task(K3, '任务三', 'topic-a'),
        ],
        4
      ),
    ])

    const mine = screen.getByRole('button', { name: /任务一/ })
    const lights = mine.querySelectorAll('.row-light')
    expect(lights).toHaveLength(1)
    expect(lights[0]!.classList.contains('row-light--blue')).toBe(true)
    // 灯是给读屏看的图，名字就是它要说的事：这盏说的是「这条归我」。
    expect(lights[0]!.getAttribute('aria-label')).toBe('My task')

    // 没主的那一条一样：这条活先当自己的。
    const unowned = screen.getByRole('button', { name: /任务三/ }).querySelectorAll('.row-light')
    expect(unowned).toHaveLength(1)
    expect(unowned[0]!.classList.contains('row-light--blue')).toBe(true)

    // 别人的活不画蓝灯，只按状态画：这条在跑，就是呼吸绿。
    const theirs = screen.getByRole('button', { name: /任务二/ })
    const theirsLights = theirs.querySelectorAll('.row-light')
    expect(theirsLights).toHaveLength(1)
    expect(theirsLights[0]!.classList.contains('row-light--breathe')).toBe(true)
    expect(theirsLights[0]!.classList.contains('row-light--blue')).toBe(false)

    // 借来的那一条还靠悬浮说明说出它为什么在这儿：这个频道里我自己的不到三条。
    // 断它必须说出的那件事，不整句比——整句比较只会在改文案时红一次
    // （王长鑫 2026-10-12 审阅 #3498 提的，`.claude/rules` 之外的那条约定）。
    expect(theirs.getAttribute('title')).toContain('Fewer than three')
  })
})
