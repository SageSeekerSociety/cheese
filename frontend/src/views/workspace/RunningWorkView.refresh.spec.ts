/** 看板会自己变新。
 *
 * 板上每一个短语都是后端算的，而后端一直在动：一块不重拉的板就是一张会骗人的快
 * 照，而它现在还是进项目的第一屏。这一份钉的是「自己变新」这件事本身，以及它必须
 * 悄悄地发生——重拉不能让正在看的列闪回骨架、不能让一次网络抖动把整块板换成
 * 「加载失败」，也不能在人已经离开这一页之后继续拉。
 */
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listProjectTasks: (...a: unknown[]) => listProjectTasks(...a) }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ topics: [{ id: 'room-1', title: '运维' }], members: [] }),
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
    presentation: { column: 'building', display_status: '排队中' },
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  listProjectTasks.mockReset()
  listProjectTasks.mockResolvedValue({ data: [task()], total: 1 })
  // shouldAdvanceTime: 假时钟照样跟着真时间走，`waitFor` 这类等待才不会永远卡住。
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
  hidden = false
})

// document.hidden 在 jsdom 里是只读的，换成一个测试说了算的取值。
let hidden = false
Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

describe('板自己变新', () => {
  it('后端那边变了，人什么都不用点，板上的话跟着变', async () => {
    const { container, findByText, queryByText } = mount()
    await findByText('排队中')

    listProjectTasks.mockResolvedValue({
      data: [task({ presentation: { column: 'building', display_status: '运行中' } })],
      total: 1,
    })
    await vi.advanceTimersByTimeAsync(20_000)

    await waitFor(() => expect(queryByText('运行中')).not.toBeNull())
    expect(queryByText('排队中')).toBeNull()
    // 还是那一张卡，不是多出来一张。
    expect(container.querySelectorAll('.board-card').length).toBe(1)
  })

  it('重拉的那一刻，正在看的东西不消失、骨架不回来', async () => {
    // 悄悄那一路不碰 loading，所以「等着」这件事根本没有在屏幕上发生过。
    let release!: (v: { data: RoomTask[]; total: number }) => void
    const { container, findByText } = mount()
    await findByText('查一下分页接口')

    listProjectTasks.mockReturnValue(new Promise((r) => (release = r)))
    await vi.advanceTimersByTimeAsync(20_000)

    expect(container.querySelector('[role="status"][aria-busy="true"]'), '骨架不该再出现一次').toBeNull()
    expect(container.querySelectorAll('.board-card').length, '卡片不该在等待期间空出来').toBe(1)

    release({ data: [task({ title: '换了个标题' })], total: 1 })
    await waitFor(() => expect(container.textContent).toContain('换了个标题'))
  })

  it('悄悄那一次失败了，板上的活还在，不整块变成「加载失败」', async () => {
    const { container, findByText, queryByText } = mount()
    await findByText('查一下分页接口')

    listProjectTasks.mockRejectedValue(new Error('network'))
    await vi.advanceTimersByTimeAsync(20_000)

    expect(queryByText('加载失败')).toBeNull()
    expect(container.querySelectorAll('.board-card').length).toBe(1)
  })

  it('第一次加载失败之后，下一次悄悄成功就自己好了', async () => {
    listProjectTasks.mockRejectedValueOnce(new Error('network'))
    const { findByText } = mount()
    await findByText('加载失败')

    listProjectTasks.mockResolvedValue({ data: [task()], total: 1 })
    await vi.advanceTimersByTimeAsync(20_000)
    await findByText('查一下分页接口')
  })

  it('人离开这一页之后就不再拉了', async () => {
    const { findByText, unmount } = mount()
    await findByText('查一下分页接口')
    const before = listProjectTasks.mock.calls.length

    unmount()
    await vi.advanceTimersByTimeAsync(120_000)
    expect(listProjectTasks.mock.calls.length, '定时器该跟着视图一起没了').toBe(before)
  })
})

describe('看不见的时候不空转', () => {
  it('标签页在后台，到点也不拉', async () => {
    const { findByText } = mount()
    await findByText('查一下分页接口')
    const before = listProjectTasks.mock.calls.length

    hidden = true
    await vi.advanceTimersByTimeAsync(60_000)
    expect(listProjectTasks.mock.calls.length).toBe(before)
  })

  it('切回来的那一下补一次 —— 看到的是此刻的板，不是离开时的', async () => {
    const { findByText, queryByText } = mount()
    await findByText('排队中')

    hidden = true
    await vi.advanceTimersByTimeAsync(60_000)
    listProjectTasks.mockResolvedValue({
      data: [task({ presentation: { column: 'building', display_status: '运行中' } })],
      total: 1,
    })

    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => expect(queryByText('运行中')).not.toBeNull())
  })
})
