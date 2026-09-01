/** 封口期提示：房间要说得出「你现在写的东西进的是哪一批」。
 *
 * 递卡的那一刻这一批封口，房间开下一棵接着干 —— 这正是房间不再被一个在飞的 PR
 * 冻住的原因。代价是封了口的房间和没封口的在屏幕上长得一模一样，于是有人接着改，
 * 再问「我改了半天，改动怎么不在 PR 上」。这一份钉的就是那句话有没有说出来。
 */
import type { Component } from 'vue'
import type { RoomTask, RoomTree, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
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
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    // 落哪一列、写哪句话，全由后端给。这一份用例不关心是哪一列，但字段必须在：
    // 前端没有一条「拿不到就自己算」的退路。
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
  return render(Panel, {
    props: { topic: ROOM, active: true },
    global: { plugins: [vuetify] },
  })
}

describe('Task Progress 的封口期提示', () => {
  it('一棵树都没封口时不说话 —— 「进这一批」是废话', async () => {
    const { queryByTestId, findByText } = mount()
    await findByText(/查一下分页接口/)
    expect(queryByTestId('seal-notice')).toBeNull()
  })

  it('封了一批、又开了下一批：说清现在写的进的是下一批', async () => {
    listRoomTrees.mockResolvedValue({
      data: [
        tree({ id: 't2', status: 'open' }),
        tree({ id: 't1', status: 'sealed', card: { id: 'c1', status: 'pr_open', pr_number: 615 } }),
      ],
      total: 2,
    })
    const { findByTestId } = mount()
    const notice = await findByTestId('seal-notice')
    expect(notice.textContent).toContain('已封口')
    // 在跑哪个 PR —— 「去看看它」的唯一线索。
    expect(notice.textContent).toContain('#615')
    expect(notice.textContent).toContain('现在写的进下一批')
  })

  it('最新那一棵自己就是封口的那棵：这个房间此刻整个在封口期', async () => {
    // 和上一条相反的下一步：那时候接着写没问题，这时候写进去会把 CI 正在检查的
    // 内容从它脚下挪走。
    listRoomTrees.mockResolvedValue({
      data: [tree({ id: 't1', status: 'sealed', card: { id: 'c1', status: 'pr_open', pr_number: 615 } })],
      total: 1,
    })
    const { findByTestId } = mount()
    const notice = await findByTestId('seal-notice')
    expect(notice.textContent).toContain('现在别再往里写')
    expect(notice.textContent).not.toContain('现在写的进下一批')
  })

  it('已经合了的那批不算在飞 —— 它不该一直挂着一句提示', async () => {
    listRoomTrees.mockResolvedValue({
      data: [tree({ id: 't2', status: 'open' }), tree({ id: 't1', status: 'merged' })],
      total: 2,
    })
    const { queryByTestId, findByText } = mount()
    await findByText(/查一下分页接口/)
    expect(queryByTestId('seal-notice')).toBeNull()
  })

  it('问不到批次就不提示，但清单照常 —— 一条请求失败不该把整格变成「加载失败」', async () => {
    listRoomTrees.mockRejectedValue(new Error('boom'))
    const { queryByTestId, findByText } = mount()
    await findByText(/查一下分页接口/)
    await waitFor(() => expect(queryByTestId('seal-notice')).toBeNull())
  })
})
