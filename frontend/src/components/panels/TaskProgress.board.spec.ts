/** 房间总览里的看板：和项目那块板同一套列、同一套短语，范围缩到一个房间。
 *
 * 「同一套」是这份用例要钉的东西。两处各叫各的名字，人就得在脑子里做一次翻译，
 * 而那次翻译迟早会错——这正是把状态收到后端算一次之后还要在前端共用一个模块的
 * 理由。
 */
import type { Component } from 'vue'
import type { RoomTask, RoomTree, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listRoomTasks = vi.fn()
const listRoomTrees = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listRoomTasks: (...a: unknown[]) => listRoomTasks(...a),
    listRoomTrees: (...a: unknown[]) => listRoomTrees(...a),
  }
})

import { BOARD_COLUMNS } from '@/lib/board'

import TaskProgress from './TaskProgress.vue'

const Panel = TaskProgress as unknown as Component

const ROOM: Topic = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '运维',
  kind: 'topic',
  status: 'active',
  created_at: '2026-08-23T00:00:00Z',
}

function task(over: Partial<RoomTask> = {}): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: '查一下分页接口',
    status: 'open',
    residency: 'idle',
    tree_id: 't1',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'building', display_status: '运行中' },
    ...over,
  }
}

function tree(over: Partial<RoomTree> = {}): RoomTree {
  return { id: 't1', status: 'open', created_at: '2026-08-23T00:00:00Z', ...over }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  listRoomTasks.mockResolvedValue({ data: [task()], total: 1 })
  listRoomTrees.mockResolvedValue({ data: [tree()], total: 1 })
})

function mount() {
  return render(Panel, { props: { topic: ROOM, active: true }, global: { plugins: [vuetify] } })
}

function titlesInColumn(container: HTMLElement, column: string): string[] {
  const head = container.querySelector(`[data-column="${column}"]`)
  const list = head?.nextElementSibling
  return [...(list?.querySelectorAll('.task-row__line1') ?? [])].map((n) => (n.textContent ?? '').trim())
}

describe('列和短语跟项目那块板是同一套', () => {
  it('列的顺序和名字一字不差', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.querySelectorAll('[data-column]').length).toBe(BOARD_COLUMNS.length))
    const names = [...container.querySelectorAll('[data-column]')].map((n) => ({
      key: n.getAttribute('data-column'),
      label: n.querySelectorAll('span')[1]?.textContent?.trim(),
    }))
    expect(names).toEqual(BOARD_COLUMNS.map((c) => ({ key: c.key, label: c.label })))
  })

  it('行上写的是后端那句话，不是这一段自己想的词', async () => {
    listRoomTasks.mockResolvedValue({
      data: [task({ presentation: { column: 'needs_you', display_status: '交付被退回' } })],
      total: 1,
    })
    const { findByText } = mount()
    await findByText('交付被退回')
  })

  it('活按后端给的列分开', async () => {
    listRoomTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '甲', presentation: { column: 'building', display_status: '空闲' } }),
        task({ id: 'b', title: '乙', presentation: { column: 'needs_you', display_status: '等待验收' } }),
      ],
      total: 2,
    })
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'needs_you')).toEqual(['第 2 件：乙']))
    expect(titlesInColumn(container, 'building')).toEqual(['第 1 件：甲'])
  })

  it('空的那一列也留着列头和 0', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('[data-column="needs_you"]')).not.toBeNull())
    const head = container.querySelector('[data-column="needs_you"]')
    expect(head?.querySelector('.task-progress__group-count')?.textContent?.trim()).toBe('0')
  })
})

describe('已完成收在最底下', () => {
  it('折起来时件数说得出来，展开后「已采纳」和「已收工」分得清', async () => {
    listRoomTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '甲', presentation: { column: 'done', display_status: '已采纳' } }),
        task({ id: 'b', title: '乙', presentation: { column: 'done', display_status: '已收工' } }),
      ],
      total: 2,
    })
    const { container, findByText, queryByText } = mount()
    const fold = await waitFor(() => {
      const el = container.querySelector('.task-progress__group--fold')
      if (!el) throw new Error('还没渲染出折叠行')
      return el as HTMLElement
    })
    expect(fold.querySelector('.task-progress__group-count')?.textContent?.trim()).toBe('2')
    expect(queryByText('已采纳')).toBeNull()

    await fireEvent.click(fold)
    await findByText('已采纳')
    await findByText('已收工')
  })
})

describe('标题旁边那个数', () => {
  it('说的是有几件在等人 —— 打开一个房间最该先看到的数', async () => {
    listRoomTasks.mockResolvedValue({
      data: [
        task({ id: 'a', presentation: { column: 'needs_you', display_status: '等待验收' } }),
        task({ id: 'b', presentation: { column: 'needs_you', display_status: '检查未通过' } }),
        task({ id: 'c', presentation: { column: 'building', display_status: '运行中' } }),
      ],
      total: 3,
    })
    const { container } = mount()
    await waitFor(() =>
      expect(container.querySelector('.task-progress__tally')?.textContent?.replace(/\s+/g, '')).toBe(
        '3件，2件等你'
      )
    )
  })
})

describe('第 N 件的编号不跟着列走', () => {
  it('编号按派活的先后，排在哪一列都不变', async () => {
    // 编号是人在对话里指代一条活的方式（「第 3 件卡住了」）。跟着分列变的编号
    // 说的是别的活。
    listRoomTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '老的', created_at: '2026-08-01T00:00:00Z' }),
        task({
          id: 'b',
          title: '新的',
          created_at: '2026-08-09T00:00:00Z',
          presentation: { column: 'needs_you', display_status: '等待验收' },
        }),
      ],
      total: 2,
    })
    const { findByText } = mount()
    await findByText(/第 1 件：老的/)
    await findByText(/第 2 件：新的/)
  })
})
