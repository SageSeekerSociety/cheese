/** 「只看我的」。
 *
 * 一个项目上百个房间，「待处理」那一列里大部分不是待你处理、是等别人。所以板上要有一个
 * 开关——而它必须住在地址栏里：一刷新就丢会让人反复点，写进地址还顺带让「我手上这
 * 些」变成一条能发出去的链接。
 *
 * 筛完之后列不能消失，计数也要说得出总数：只写筛过的那个数，人会以为活丢了。
 */
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listProjectTasks: (...a: unknown[]) => listProjectTasks(...a) }
})

// 地址栏。测试改它，视图就该跟着变——这正是「开关住在地址里」的意思。
let query: Record<string, string> = {}
const replace = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useRoute: () => ({
    get query() {
      return query
    },
  }),
}))

let handle = 'n1ctheboy'
vi.mock('@/me', () => ({ myHandle: () => handle }))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    topics: [{ id: 'room-1', title: '运维' }],
    members: [
      { user_handle: 'n1ctheboy', role: 'lead', name: '奶酪' },
      { user_handle: 'ligan', role: 'member', name: '李干' },
    ],
  }),
}))

import RunningWorkView from './RunningWorkView.vue'

const Board = RunningWorkView as unknown as Component

function task(over: Partial<RoomTask> = {}): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: '查一下分页接口',
    status: 'open',
    owner_handle: 'ligan',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'needs_you', display_status: '等待验收' },
    ...over,
  }
}

/** 三件「待处理」，其中一件是我的；外加一件别人的「施工中」。 */
const MIXED = [
  task({ id: 'a', title: '我的那件', owner_handle: 'n1ctheboy' }),
  task({ id: 'b', title: '别人的一', owner_handle: 'ligan' }),
  task({ id: 'c', title: '别人的二', owner_handle: 'ligan' }),
  task({
    id: 'd',
    title: '别人在施工',
    owner_handle: 'ligan',
    presentation: { column: 'building', display_status: '运行中' },
  }),
]

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  query = {}
  handle = 'n1ctheboy'
  replace.mockReset()
  listProjectTasks.mockReset()
  listProjectTasks.mockResolvedValue({ data: MIXED, total: MIXED.length })
})

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function titlesInColumn(container: Element, column: string): string[] {
  return Array.from(container.querySelectorAll(`[data-column="${column}"] .board-card__title`)).map((n) =>
    (n.textContent ?? '').trim()
  )
}

function countOf(container: Element, column: string): string {
  return container.querySelector(`[data-column="${column}"] .board-col__count`)?.textContent?.trim() ?? ''
}

function toggle(container: Element): HTMLElement {
  return container.querySelector('.board__mine') as HTMLElement
}

describe('开关默认是关的', () => {
  it('地址里没有 mine 的时候，板上是所有人的活', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(4))
    expect(toggle(container).getAttribute('aria-pressed')).toBe('false')
    // 开关关着的时候不写分母：没有取景，就没有「筛过的 / 全部」这回事。
    expect(countOf(container, 'needs_you')).toBe('3')
  })
})

describe('开关住在地址里', () => {
  it('点一下就把 mine=1 写进地址，而不是记在组件里', async () => {
    const { container } = mount()
    await waitFor(() => expect(toggle(container)).not.toBeNull())
    await fireEvent.click(toggle(container))
    expect(replace).toHaveBeenCalledWith({ query: { mine: '1' } })
  })

  it('再点一下就把它从地址里去掉', async () => {
    query = { mine: '1' }
    const { container } = mount()
    await waitFor(() => expect(toggle(container)).not.toBeNull())
    await fireEvent.click(toggle(container))
    expect(replace).toHaveBeenCalledWith({ query: {} })
  })

  it('地址里原有的东西不会被这个开关抹掉', async () => {
    query = { tab: 'x' }
    const { container } = mount()
    await waitFor(() => expect(toggle(container)).not.toBeNull())
    await fireEvent.click(toggle(container))
    expect(replace).toHaveBeenCalledWith({ query: { tab: 'x', mine: '1' } })
  })

  it('带着 ?mine=1 的链接直接打开，就是筛过的那一屏', async () => {
    // 这条正是「写进地址」买到的东西：这一屏可以发给别人。
    query = { mine: '1' }
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'needs_you')).toEqual(['我的那件']))
    expect(toggle(container).getAttribute('aria-pressed')).toBe('true')
  })
})

describe('筛完之后板还是一块板', () => {
  it('列一个都没少，被筛空的那列留着列头和 0', async () => {
    query = { mine: '1' }
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'needs_you')).toEqual(['我的那件']))
    // 「施工中」那一件是别人的，筛没了——但那一列还在原地。位置本身是信息。
    expect(container.querySelector('[data-column="building"]')).not.toBeNull()
    expect(container.querySelector('[data-column="delivering"]')).not.toBeNull()
    expect(countOf(container, 'building')).toBe('0 / 1')
  })

  it('计数说得出总数，人才不会以为活丢了', async () => {
    query = { mine: '1' }
    const { container } = mount()
    await waitFor(() => expect(countOf(container, 'needs_you')).toBe('1 / 3'))
  })

  it('筛到一件不剩的时候，每一列自己说「暂无归你的任务」', async () => {
    // 「暂无施工中的任务」在这一刻是句错话：那一列有活，只是不归你。
    query = { mine: '1' }
    listProjectTasks.mockResolvedValue({ data: [task({ owner_handle: 'ligan' })], total: 1 })
    const { container, getAllByText, queryByText } = mount()
    await waitFor(() => expect(countOf(container, 'needs_you')).toBe('0 / 1'))
    expect(getAllByText('暂无归你的任务').length).toBe(3)
    expect(queryByText('暂无施工中的任务')).toBeNull()
    // 顶上那行数的仍然是整块板：它说的是这个项目有多少活，和取景无关。
    expect(container.querySelector('.board__head p')?.textContent?.replace(/\s+/g, '')).toBe('待处理1')
  })

  it('筛掉之后那一列的下一步提示也收起来 —— 「在房间里说明要做什么」在这一刻是句错话', async () => {
    query = { mine: '1' }
    listProjectTasks.mockResolvedValue({ data: [task({ owner_handle: 'ligan' })], total: 1 })
    const { container, queryByText } = mount()
    await waitFor(() => expect(countOf(container, 'needs_you')).toBe('0 / 1'))
    expect(queryByText('在房间里说明要做什么，芝士会把它拆成任务')).toBeNull()
  })

  it('「房间满员」数的是整块板，不是筛过的那一份', async () => {
    // 房间满没满和「这是谁的活」无关。按筛过的结果数，开关一开这个提示就凭空没了。
    const running = { column: 'building', display_status: '运行中' } as const
    query = { mine: '1' }
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'mine', title: '我的', owner_handle: 'n1ctheboy', presentation: { ...running } }),
        ...['x', 'y', 'z'].map((id) => task({ id, owner_handle: 'ligan', presentation: { ...running } })),
      ],
      total: 4,
    })
    const { findByText } = mount()
    await findByText('房间满员')
  })
})

describe('已完成那条折叠行', () => {
  it('筛完之后还在，件数写成「0 / 2」—— 抹掉它会读成这个项目从没交付过什么', async () => {
    query = { mine: '1' }
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', owner_handle: 'ligan', presentation: { column: 'done', display_status: '已收工' } }),
        task({ id: 'b', owner_handle: 'ligan', presentation: { column: 'done', display_status: '已收工' } }),
      ],
      total: 2,
    })
    const { container } = mount()
    await waitFor(() =>
      expect(container.querySelector('.board__done .board-col__count')?.textContent?.trim()).toBe('0 / 2')
    )
  })
})

describe('取不到登录身份的时候', () => {
  it('不画这个开关 —— 按空 handle 筛只会把整块板清空', async () => {
    handle = ''
    const { container } = mount()
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(4))
    expect(toggle(container)).toBeNull()
  })
})
