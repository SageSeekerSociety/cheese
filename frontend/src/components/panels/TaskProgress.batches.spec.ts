/** 批次清单：一批活出一个 PR，屏幕上要分得清哪个是哪个。
 *
 * 「PR状态也在总览里面显示吧，不然多个 PR 感觉有点混乱」——在这份清单之前，PR 号
 * 只在当前那张验收卡上出现过一次，一个房间攒了几批活之后就没有任何地方回答得了
 * 「我那条活最后从哪个 PR 出去」。这一份钉的是：每批一行、各带各的 PR、当前那批
 * 明说「现在写的进这一批」。
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
    tree_id: 't1',
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

describe('总览里的批次清单', () => {
  it('只有一批、还没开出 PR 时不列 —— 那时候「哪一批」不是个问题', async () => {
    const { queryByTestId, findByText } = mount()
    await findByText(/查一下分页接口/)
    expect(queryByTestId('batch-list')).toBeNull()
  })

  it('几批并存时每批一行，各带各的 PR —— 这就是「多个 PR 有点混乱」的答案', async () => {
    listRoomTrees.mockResolvedValue({
      data: [
        tree({ id: 't3', status: 'open', created_at: '2026-08-27T00:00:00Z' }),
        tree({
          id: 't2',
          status: 'sealed',
          created_at: '2026-08-26T00:00:00Z',
          sealed_at: '2026-08-27T00:00:00Z',
          card: { id: 'c2', status: 'pr_open', pr_number: 620, pr_url: 'https://github.com/o/r/pull/620' },
        }),
        tree({
          id: 't1',
          status: 'merged',
          created_at: '2026-08-25T00:00:00Z',
          merged_at: '2026-08-26T00:00:00Z',
          card: { id: 'c1', status: 'accepted', pr_number: 618, pr_url: 'https://github.com/o/r/pull/618' },
        }),
      ],
      total: 3,
    })
    const { findByTestId } = mount()

    // 每一批读得出：它是第几批、什么状态、骑在哪个 PR 上。
    const current = await findByTestId('batch-t3')
    expect(current.textContent).toContain('第 3 批')
    expect(current.textContent).toContain('在收活')
    expect(current.textContent).toContain('还没开 PR')

    const sealed = await findByTestId('batch-t2')
    expect(sealed.textContent).toContain('已封口')
    expect(sealed.textContent).toContain('#620')

    const merged = await findByTestId('batch-t1')
    expect(merged.textContent).toContain('已合并')
    expect(merged.textContent).toContain('#618')

    // 一个 PR 号只出现在它自己那一行上。混乱正是从「620 和 618 都在，谁是谁不知道」来的。
    expect(sealed.textContent).not.toContain('#618')
    expect(merged.textContent).not.toContain('#620')
  })

  it('说清现在写的东西进的是哪一批', async () => {
    listRoomTrees.mockResolvedValue({
      data: [
        tree({ id: 't2', status: 'open', created_at: '2026-08-27T00:00:00Z' }),
        tree({ id: 't1', status: 'merged', created_at: '2026-08-25T00:00:00Z', merged_at: '2026-08-26T00:00:00Z' }),
      ],
      total: 2,
    })
    const { findByTestId } = mount()
    expect((await findByTestId('batch-t2')).textContent).toContain('现在写的进这一批')
    expect((await findByTestId('batch-t1')).textContent).not.toContain('现在写的进这一批')
  })

  it('封了口的房间没有「现在写的进这一批」可说 —— 最新那一棵自己就在 CI 上', async () => {
    listRoomTrees.mockResolvedValue({
      data: [
        tree({
          id: 't1',
          status: 'sealed',
          sealed_at: '2026-08-27T00:00:00Z',
          card: { id: 'c1', status: 'pr_open', pr_number: 620 },
        }),
      ],
      total: 1,
    })
    const { findByTestId } = mount()
    const row = await findByTestId('batch-t1')
    expect(row.textContent).not.toContain('现在写的进这一批')
  })

  it('每批里有几件活，从活自己带的 tree_id 数出来', async () => {
    listRoomTasks.mockResolvedValue({
      data: [
        task({ id: 'a', title: '甲', tree_id: 't2' }),
        task({ id: 'b', title: '乙', tree_id: 't2' }),
        task({ id: 'c', title: '丙', tree_id: 't1' }),
      ],
      total: 3,
    })
    listRoomTrees.mockResolvedValue({
      data: [
        tree({ id: 't2', status: 'open', created_at: '2026-08-27T00:00:00Z' }),
        tree({
          id: 't1',
          status: 'merged',
          created_at: '2026-08-25T00:00:00Z',
          card: { id: 'c1', status: 'accepted', pr_number: 618 },
        }),
      ],
      total: 2,
    })
    const { findByTestId } = mount()
    expect((await findByTestId('batch-t2')).textContent).toContain('2 件活')
    expect((await findByTestId('batch-t1')).textContent).toContain('1 件活')
  })

  it('一件派出去的活都没有的那批不写件数 —— 房间自己写的东西也在批次里', async () => {
    listRoomTasks.mockResolvedValue({ data: [task({ tree_id: 't2' })], total: 1 })
    listRoomTrees.mockResolvedValue({
      data: [
        tree({ id: 't2', status: 'open', created_at: '2026-08-27T00:00:00Z' }),
        tree({
          id: 't1',
          status: 'merged',
          created_at: '2026-08-25T00:00:00Z',
          card: { id: 'c1', status: 'accepted', pr_number: 618 },
        }),
      ],
      total: 2,
    })
    const { findByTestId } = mount()
    expect((await findByTestId('batch-t1')).textContent).not.toContain('件活')
  })

  it('PR 号是可点的 —— 「去看看它」不该还要自己去 GitHub 上找', async () => {
    listRoomTrees.mockResolvedValue({
      data: [
        tree({ id: 't2', status: 'open', created_at: '2026-08-27T00:00:00Z' }),
        tree({
          id: 't1',
          status: 'sealed',
          created_at: '2026-08-25T00:00:00Z',
          card: { id: 'c1', status: 'pr_open', pr_number: 620, pr_url: 'https://github.com/o/r/pull/620' },
        }),
      ],
      total: 2,
    })
    const { findByText } = mount()
    const link = await findByText('PR #620')
    expect(link.getAttribute('href')).toBe('https://github.com/o/r/pull/620')
  })

  it('快检红了要看得见 —— 它不拦任何人，将要验收的人得知道', async () => {
    listRoomTrees.mockResolvedValue({
      data: [
        tree({ id: 't2', status: 'open', created_at: '2026-08-27T00:00:00Z', last_check_ok: false }),
        tree({ id: 't1', status: 'merged', created_at: '2026-08-25T00:00:00Z', last_check_ok: true }),
      ],
      total: 2,
    })
    const { findByTestId } = mount()
    expect((await findByTestId('batch-t2')).textContent).toContain('快检没过')
    expect((await findByTestId('batch-t1')).textContent).not.toContain('快检没过')
  })

  it('批次多了折起来，但说出折了几批 —— 悄悄截断会让人以为这就是全部', async () => {
    listRoomTrees.mockResolvedValue({
      data: Array.from({ length: 7 }, (_, i) => {
        const n = 7 - i
        return tree({
          id: `t${n}`,
          status: n === 7 ? 'open' : 'merged',
          created_at: `2026-08-${String(10 + n).padStart(2, '0')}T00:00:00Z`,
          card: { id: `c${n}`, status: 'accepted', pr_number: 600 + n },
        })
      }),
      total: 7,
    })
    const { findByTestId, findByText, queryByTestId } = mount()
    await findByTestId('batch-t7')
    expect(queryByTestId('batch-t1')).toBeNull()

    const more = await findByText(/还有 2 批更早的/)
    await fireEvent.click(more)
    await waitFor(() => expect(queryByTestId('batch-t1')).not.toBeNull())
  })

  it('问不到批次时清单照常 —— 一条请求失败不该把整格变成「加载失败」', async () => {
    listRoomTrees.mockRejectedValue(new Error('boom'))
    const { queryByTestId, findByText } = mount()
    await findByText(/查一下分页接口/)
    await waitFor(() => expect(queryByTestId('batch-list')).toBeNull())
  })
})

describe('这一段只说中文', () => {
  it('标题和每条活的编号都是中文', async () => {
    const { findByText, queryByText } = mount()
    await findByText('看板')
    expect(queryByText('Task Progress')).toBeNull()
    await findByText(/第 1 件：查一下分页接口/)
  })
})
