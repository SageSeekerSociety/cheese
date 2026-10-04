// 列头那个数：数据到货之前，计数槽里一个数字都不能有。
//
// 冷加载时四列先摆出骨架，而计数槽里原来写的是 0，一秒后才跳到真实的 44 / 2 / 1
// —— 用户把那个 0 读成「我的活没了」。这一份钉住：数据没到，计数槽里就没有数字
// （四列，包括最右边「做出了什么」那一列）；数据到了，真实的数才出现，该是 0 的
// 那一列这时候才写 0。
import type { Component } from 'vue'
import type { ProjectArtifact } from '@/api'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()
const listProjectArtifacts = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listProjectTasks: (...a: unknown[]) => listProjectTasks(...a),
    listProjectArtifacts: (...a: unknown[]) => listProjectArtifacts(...a),
    getProjectSite: () => Promise.resolve({ site: null, source_revision: null, candidates: [], can_publish: false }),
  }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ topics: [{ id: 'room-1', title: '运维' }], members: [] }),
}))

import RunningWorkView from './RunningWorkView.vue'

import { setLocale } from '@/i18n'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

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
    presentation: { column: 'building', phrase: 'running' },
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

/** 一次拿得住的请求：先挂着，等测试自己决定什么时候让数据到达。 */
function pending<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

beforeEach(() => {
  listProjectTasks.mockReset()
  listProjectArtifacts.mockReset()
})

function countSlots(container: Element): Element[] {
  return Array.from(container.querySelectorAll('.board-col__count'))
}

describe('计数到货之前不写 0', () => {
  it('四列的计数槽在数据到达前都没有数字，到了才写', async () => {
    const tasks = pending<{ data: RoomTask[]; total: number }>()
    const artifacts = pending<{ data: ProjectArtifact[]; total: number }>()
    listProjectTasks.mockReturnValue(tasks.promise)
    listProjectArtifacts.mockReturnValue(artifacts.promise)

    const { container } = render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })

    // 四列（三条任务列 + 做出什么）都已经就位，计数槽也都在。
    await waitFor(() => expect(container.querySelectorAll('.board-col').length).toBe(4))
    const before = countSlots(container)
    expect(before, '四列的计数槽都该在').toHaveLength(4)
    for (const slot of before) {
      expect(slot.textContent?.trim() ?? '', '数据没到，计数槽里不该有数字').not.toMatch(/\d/)
    }

    // 数据到货：真实计数才出现，该是 0 的那一列这时候才写 0。
    tasks.resolve({
      data: [
        task({ id: 'a', presentation: { column: 'building', phrase: 'running' } }),
        task({ id: 'b', presentation: { column: 'building', phrase: 'not_started' } }),
        task({ id: 'c', presentation: { column: 'needs_you', phrase: 'awaiting_review' } }),
      ],
      total: 3,
    })
    artifacts.resolve({ data: [{ id: 'art-1', name: '设计稿' } as ProjectArtifact], total: 1 })

    await waitFor(() =>
      expect(container.querySelector('[data-column="building"] .board-col__count')?.textContent?.trim()).toBe('2')
    )
    expect(container.querySelector('[data-column="delivering"] .board-col__count')?.textContent?.trim()).toBe('0')
    expect(container.querySelector('[data-column="needs_you"] .board-col__count')?.textContent?.trim()).toBe('1')
    expect(container.querySelector('.board-col--made .board-col__count')?.textContent?.trim()).toBe('1')
  })

  it('空项目：计数槽先空着，真读到空的时候才写 0', async () => {
    const tasks = pending<{ data: RoomTask[]; total: number }>()
    const artifacts = pending<{ data: ProjectArtifact[]; total: number }>()
    listProjectTasks.mockReturnValue(tasks.promise)
    listProjectArtifacts.mockReturnValue(artifacts.promise)

    const { container } = render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
    await waitFor(() => expect(container.querySelectorAll('.board-col').length).toBe(4))
    for (const slot of countSlots(container)) {
      expect(slot.textContent?.trim() ?? '', '数据没到，计数槽里不该有数字').not.toMatch(/\d/)
    }

    // 真的读到空：这就是一个真实的 0，这时候写出来是对的。
    tasks.resolve({ data: [], total: 0 })
    artifacts.resolve({ data: [], total: 0 })
    await waitFor(() =>
      expect(container.querySelector('[data-column="building"] .board-col__count')?.textContent?.trim()).toBe('0')
    )
  })
})
