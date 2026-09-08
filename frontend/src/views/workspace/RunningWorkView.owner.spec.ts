/** 卡上的人，和卡上「这会儿在不在动」。
 *
 * 「谁在做」是一个人，不是一个 handle：`n1ctheboy` 这种串认得出来的只有他本人。名
 * 册里有昵称和他自己挑的头像，卡上就该是那两样——而没挑过头像的人必须是按 handle
 * 哈希出来的彩色首字母，不是一张所有人共用的默认脸。
 *
 * 另一件：色点是**列级**的，所以「施工中」那一列里在跑的和排队的原来长得一模一
 * 样。这两件事对看的人不是一回事。
 */
import type { Component } from 'vue'
import type { ProjectMemberRow, RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { avatarColor } from '@/utils/avatar'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listProjectTasks: (...a: unknown[]) => listProjectTasks(...a) }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

let members: ProjectMemberRow[] = []
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    topics: [{ id: 'room-1', title: '运维' }],
    get members() {
      return members
    },
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
    owner_handle: 'n1ctheboy',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'building', display_status: '排队中' },
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  members = [{ user_handle: 'n1ctheboy', role: 'lead', name: '奶酪', avatar_id: 7 }]
  listProjectTasks.mockReset()
  listProjectTasks.mockResolvedValue({ data: [task()], total: 1 })
})

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function owner(container: Element): Element {
  return container.querySelector('.board-card__who') as Element
}

describe('卡上的「谁在做」是一个人', () => {
  it('写的是名册上的昵称，不是英文 handle', async () => {
    const { findByText, queryByText } = mount()
    await findByText('奶酪')
    expect(queryByText('n1ctheboy')).toBeNull()
  })

  it('他自己挑过头像，卡上画的就是那一张', async () => {
    const { container } = mount()
    await waitFor(() => expect(owner(container)).not.toBeNull())
    const img = owner(container).querySelector('img')
    expect(img?.getAttribute('src')).toContain('/avatars/7')
  })

  it('没挑过头像的人画的是他自己那一块颜色的首字母，不是共用的默认脸', async () => {
    // avatar_id 为 null 就是「从来没挑过」。拿它去取 /avatars/default 会让所有没
    // 挑过头像的人在板上长同一张脸——那比没有头像更糟。
    members = [{ user_handle: 'n1ctheboy', role: 'lead', name: '奶酪', avatar_id: null }]
    const { container } = mount()
    await waitFor(() => expect(owner(container)).not.toBeNull())

    expect(owner(container).querySelector('img'), '不该去取任何一张图').toBeNull()
    const chip = owner(container).querySelector('.board-card__avatar') as HTMLElement
    expect(chip.textContent?.trim()).toBe('奶')
    // 颜色是从 handle 哈希出来的，所以同一个人在哪一屏都是同一块颜色。
    expect(chip.style.backgroundColor).not.toBe('')
    expect(chip.style.backgroundColor).toBe(avatarColor('n1ctheboy'))
  })

  it('两个没挑过头像的人不共用一块颜色', async () => {
    members = [
      { user_handle: 'n1ctheboy', role: 'lead', name: '奶酪', avatar_id: null },
      { user_handle: 'ligan', role: 'member', name: '李干', avatar_id: null },
    ]
    listProjectTasks.mockResolvedValue({
      data: [task({ id: 'a' }), task({ id: 'b', owner_handle: 'ligan' })],
      total: 2,
    })
    const { container } = mount()
    await waitFor(() => expect(container.querySelectorAll('.board-card__avatar').length).toBe(2))
    const [one, two] = Array.from(container.querySelectorAll<HTMLElement>('.board-card__avatar'))
    expect(one.style.backgroundColor).not.toBe(two.style.backgroundColor)
  })

  it('头像图加载不出来就退回彩色首字母，不留一个破图', async () => {
    const { container } = mount()
    await waitFor(() => expect(owner(container).querySelector('img')).not.toBeNull())

    await fireEvent.error(owner(container).querySelector('img') as HTMLElement)
    await waitFor(() => expect(owner(container).querySelector('img')).toBeNull())
    expect((owner(container).querySelector('.board-card__avatar') as HTMLElement).textContent?.trim()).toBe('奶')
  })

  it('名册里查不到这个 handle 时，把 handle 原样显示出来，不留空白', async () => {
    members = []
    const { findByText, container } = mount()
    await findByText('n1ctheboy')
    // 查不到就不去取图：getAvatarUrl(undefined) 会给他配一张 /avatars/default。
    expect(owner(container).querySelector('img')).toBeNull()
  })

  it('owner_handle 是 `session` 这种不是人的值时也不去取图', async () => {
    // 后端会把非人的执行体写在这个字段上。它不在名册里，所以走的是同一条退路。
    members = []
    listProjectTasks.mockResolvedValue({ data: [task({ owner_handle: 'session' })], total: 1 })
    const { findByText, container } = mount()
    await findByText('session')
    expect(owner(container).querySelector('img')).toBeNull()
  })

  it('没有负责人的活还是说「暂无负责人」', async () => {
    listProjectTasks.mockResolvedValue({ data: [task({ owner_handle: null })], total: 1 })
    const { findByText, container } = mount()
    await findByText('暂无负责人')
    expect(container.querySelector('.board-card__avatar')).toBeNull()
  })
})

describe('同一列里，在跑的和闲着的不再长得一样', () => {
  it('「运行中」那张卡的状态点带上呼吸，排队的那张没有', async () => {
    listProjectTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '在跑的', presentation: { column: 'building', display_status: '运行中' } }),
        task({ id: 'b', title: '排队的', presentation: { column: 'building', display_status: '排队中' } }),
      ],
      total: 2,
    })
    const { container } = mount()
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(2))

    const cards = Array.from(container.querySelectorAll('.board-card'))
    const running = cards.find((c) => c.textContent?.includes('在跑的')) as Element
    const queued = cards.find((c) => c.textContent?.includes('排队的')) as Element
    expect(running.querySelector('.board-card__status .running-dot')).not.toBeNull()
    expect(queued.querySelector('.board-card__status .running-dot')).toBeNull()
  })

  it('后端换个别的短语，那张卡就不呼吸了 —— 「在跑」不是前端自己推的', async () => {
    listProjectTasks.mockResolvedValue({
      data: [task({ presentation: { column: 'building', display_status: '等待检查' } })],
      total: 1,
    })
    const { container } = mount()
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(1))
    expect(container.querySelector('.running-dot')).toBeNull()
  })
})
