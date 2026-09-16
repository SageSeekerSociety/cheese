// 看板在活到达之前长什么样。
//
// 板的框架是固定的：三列、每列一个列头，跟有几件活无关。所以等待期间该画的不是
// 一块占满整页的灰，而是那个框架本身 —— 列先就位，卡的位置上放骨架。这一份钉的
// 就是「卡到齐的那一刻，板不动」：列头一直在，骨架换成卡，别的什么都没挪。
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

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
    presentation: { column: 'building', display_status: '运行中' },
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

/** 一次拿得住的请求：先挂着，等测试自己决定什么时候让活到达。 */
function pending<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

beforeEach(() => {
  listProjectTasks.mockReset()
})

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function columnNames(container: Element): string[] {
  return Array.from(container.querySelectorAll('.board-col__name')).map((n) => n.textContent?.trim() ?? '')
}

describe('活还在路上的看板', () => {
  it('列已经在了，卡的位置上画的是骨架', async () => {
    const gate = pending<{ data: RoomTask[]; total: number }>()
    listProjectTasks.mockReturnValue(gate.promise)
    const { container } = mount()

    await waitFor(() =>
      expect(
        container.querySelectorAll('.board-col [role="status"][aria-busy="true"]').length,
        '每一列都该说出自己在等'
      ).toBe(3)
    )
    // 板的框架和活无关，所以它没有理由等：列头在这一刻就已经是最终的样子。
    expect(columnNames(container)).toEqual(['施工中', '交付中', '待处理'])
    expect(container.querySelector('.v-progress-circular'), '板的形状是已知的，不该用转圈').toBeNull()

    gate.resolve({ data: [task()], total: 1 })
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(1))
  })

  it('活到齐，骨架走干净，列头还在原地', async () => {
    const gate = pending<{ data: RoomTask[]; total: number }>()
    listProjectTasks.mockReturnValue(gate.promise)
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())
    const before = columnNames(container)

    gate.resolve({ data: [task({ title: '甲' })], total: 1 })
    await waitFor(() => expect(container.querySelectorAll('.board-card').length).toBe(1))

    expect(container.querySelector('[role="status"][aria-busy="true"]')).toBeNull()
    expect(columnNames(container), '列头在两个时刻是同一串，板才不会跳').toEqual(before)
  })

  it('一件活都没有的时候，说的是「暂无派出去的任务」，不是接着画骨架', async () => {
    // 空板和「还没到」在屏幕上必须是两回事，所以这里等着看骨架先出现、再消失：
    // 一上来 rows 也是空的，不卡着这一步就分不清测的是哪一刻。
    const gate = pending<{ data: RoomTask[]; total: number }>()
    listProjectTasks.mockReturnValue(gate.promise)
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())

    gate.resolve({ data: [], total: 0 })
    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).toBeNull())
    expect(container.textContent).toContain('暂无派出去的任务')
  })
})
