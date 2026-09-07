/** 点开一张卡就在房间里往下钻一层，不是跳到一个新地点。
 *
 * 一件活不是地点：做它的分身住在房间的会话里，它没有名册、没有归档、没有自己的
 * 一轮。所以这份用例钉的是「卡按卡渲染」——标题、状态词、简报、结论、它自己的
 * 对话，全部从 `/topics/{room}/tasks/{card}` 那一份载荷来，状态词一个字不推。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Component } from 'vue'
import type { Block, RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getRoomTask = vi.fn()
const sayOnRoomTask = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getRoomTask: (...a: unknown[]) => getRoomTask(...a),
    sayOnRoomTask: (...a: unknown[]) => sayOnRoomTask(...a),
  }
})

import PanelCard from './PanelCard.vue'

const Panel = PanelCard as unknown as Component

function block(over: Partial<Block> = {}): Block {
  return {
    id: 'b1',
    topic_id: 'room-1',
    author: 'alice',
    author_type: 'human',
    content: '这条先别动 routes',
    kind: 'message',
    created_at: '2026-09-06T01:00:00Z',
    ...over,
  } as Block
}

function card(over: Partial<RoomTask> = {}): RoomTask & { blocks: Block[] } {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: '接口分页',
    status: 'open',
    tree_id: 't1',
    owner_handle: 'alice',
    brief: '加 cursor 参数',
    created_at: '2026-09-06T00:00:00Z',
    updated_at: '2026-09-06T01:00:00Z',
    presentation: { column: 'building', display_status: '运行中' },
    blocks: [block()],
    ...over,
  } as RoomTask & { blocks: Block[] }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getRoomTask.mockReset()
  sayOnRoomTask.mockReset()
  getRoomTask.mockResolvedValue(card())
  sayOnRoomTask.mockResolvedValue(block({ id: 'b2' }))
})

function mount() {
  return render(Panel, {
    props: { roomId: 'room-1', cardId: 'task-1', active: true },
    global: { plugins: [vuetify] },
  })
}

describe('一张卡按卡渲染', () => {
  it('走房间的地址问这张卡，不拿卡的 id 当地点', async () => {
    mount()
    await waitFor(() => expect(getRoomTask).toHaveBeenCalled())
    expect(getRoomTask.mock.calls[0].slice(0, 2)).toEqual(['room-1', 'task-1'])
  })

  it('状态词是后端算好的那一句，前端不自己推', async () => {
    const { getByTestId } = mount()
    await waitFor(() => expect(getByTestId('card-status').textContent).toBe('运行中'))
  })

  it('简报和结论是卡自己的两列，摆在对话上面', async () => {
    getRoomTask.mockResolvedValue(card({ conclusion: '做完了，改了三处' }))
    const { getByText, getByTestId } = mount()
    await waitFor(() => getByText('加 cursor 参数'))
    expect(getByTestId('card-conclusion').textContent).toContain('做完了，改了三处')
  })

  it('卡下的对话是这条活自己的', async () => {
    const { getByText } = mount()
    await waitFor(() => getByText('这条先别动 routes'))
  })

  it('在卡下面说话走的是这张卡的地址', async () => {
    const { container } = mount()
    await waitFor(() => expect(getRoomTask).toHaveBeenCalled())
    const input = container.querySelector('.panel-card__input') as HTMLInputElement
    await fireEvent.update(input, '再看一眼 routes')
    await fireEvent.submit(container.querySelector('.panel-card__say') as Element)
    await waitFor(() => expect(sayOnRoomTask).toHaveBeenCalled())
    expect(sayOnRoomTask.mock.calls[0].slice(0, 3)).toEqual(['room-1', 'task-1', '再看一眼 routes'])
  })

  it('「看板」把人退回去，而不是退出这个房间', async () => {
    const { emitted, getByText } = mount()
    await waitFor(() => getByText('接口分页'))
    await fireEvent.click(getByText('看板'))
    expect(emitted().back).toBeTruthy()
  })
})

// 简报可以是几千字（fulu 报告过撑爆面板）：块内必须自滚，不许把整个面板撑高。
// 源码断言钉住这条约束——它是 CSS，挂载测试量不到布局。
describe('简报块自己滚动', () => {
  it('block-body 带着 max-height 和 overflow-y', () => {
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'PanelCard.vue'), 'utf8')
    const block = src.slice(src.indexOf('.panel-card__block-body'))
    const rule = block.slice(0, block.indexOf('}'))
    expect(rule).toContain('max-height')
    expect(rule).toContain('overflow-y: auto')
  })
})
