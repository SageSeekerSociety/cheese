// @vitest-environment jsdom
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
import type { AcceptCard, Block, MergeStateInfo, RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ project: null, members: [] }) }))

const getRoomTask = vi.fn()
const sayOnRoomTask = vi.fn()
const getAcceptCards = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
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
    branch_name: 'task/one',
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
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getRoomTask.mockReset()
  sayOnRoomTask.mockReset()
  getAcceptCards.mockReset()
  // 默认没有验收卡：绝大多数用例里的卡不值得验收，验收框那一块不出现。
  getAcceptCards.mockResolvedValue({ data: [], total: 0 })
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

  it('renders AI deliverables as safe Markdown and keeps human messages literal', async () => {
    getRoomTask.mockResolvedValue({
      ...card({ brief: '**Goal**', conclusion: '[Read the report](https://example.com/report)' }),
      blocks: [
        block({ id: 'ai', author_type: 'ai', content: '**Result**\n\n- Ready\n\n<script>alert(1)</script>' }),
        block({ id: 'human', content: '**keep this literal**' }),
      ],
    })
    const { container, getByText } = mount()
    await waitFor(() => getByText('Result'))
    expect(container.querySelector('.panel-card__block-body strong')?.textContent).toBe('Goal')
    expect(container.querySelector('[data-testid="card-conclusion"] a')?.getAttribute('href')).toBe(
      'https://example.com/report'
    )
    expect(container.querySelector('.card-msg__text strong')?.textContent).toBe('Result')
    expect(container.querySelector('.card-msg__text li')?.textContent).toBe('Ready')
    expect(container.querySelector('script')).toBeNull()
    expect(getByText('**keep this literal**')).toBeTruthy()
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

// 上一条只堵住了简报一个块。验收卡（`TopicAcceptCard`）比简报更能长：检查项、批准
// 人、推荐理由、PR 上的检查每多一条它就高一截，而它和标题、元信息、结论、发言框
// 一样是 `min-height: auto` —— 收缩不到内容高度以下。整格唯一肯缩的是对话区，它被
// 压到 0 之后，多出来的部分就从面板底部溢出去、被外壳裁掉：看不见，也滚不到（马霄
// 宇报的）。
//
// 所以两件事都要成立：这一格自己是一个滚动层；对话区有一个非零的下限，别被压成一
// 个高度 0、连滚动条都没有的盒子。两条都是 CSS，挂载测试量不到布局，照上面那条的
// 老办法用源码断言钉住。
describe('面板不够高时滚得动', () => {
  const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'PanelCard.vue'), 'utf8')

  function rule(selector: string): string {
    const start = src.indexOf(`\n${selector} {`)
    if (start < 0) throw new Error(`PanelCard.vue 里没有这条规则：${selector}`)
    const body = src.slice(src.indexOf('{', start) + 1, src.indexOf('}', start))
    // 注释里也会出现 `overflow-y: auto` 这种词，先摘掉再断言，免得注释把测试骗绿。
    return body.replace(/\/\*[\s\S]*?\*\//g, '')
  }

  it('面板自己就是这一格的滚动层', () => {
    expect(rule('.panel-card')).toContain('overflow-y: auto')
  })

  it('对话区有一个非零的下限', () => {
    const timeline = rule('.panel-card__timeline')
    const min = /min-height:\s*(\d+)px/.exec(timeline)
    expect(min).not.toBeNull()
    expect(Number(min?.[1])).toBeGreaterThan(0)
    expect(timeline).toContain('overflow-y: auto')
  })
})

// 待采纳卡：形状照 `components/__tests__/TopicAcceptCardMergeState.test.ts` 那份夹具，
// 只是这张挂在 `task-1` 上——验收框是按卡拉的，`task_id` 对不上它就当没有卡。
function acceptCard(over: Partial<AcceptCard> = {}): AcceptCard {
  return {
    id: 'accept-1',
    task_id: 'task-1',
    topic_id: 'room-1',
    reviewer_handle: 'alice',
    routing_reason: '最懂',
    change_subject: 'fix: do a thing',
    change_body: null,
    status: 'pending',
    decided_by: null,
    decided_at: null,
    note: '',
    note_level: null,
    created_at: '2026-09-06T02:00:00Z',
    gate_passed_at: null,
    gate_output: '',
    approvals: [],
    approvals_required: 1,
    pr_number: null,
    pr_url: null,
    // 平台 lane 的常态（#363 拍板）：没有外部检查可读，后端直接下发 clean。
    merge_state: {
      state: 'clean',
      who: 'human',
      reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
      head_sha: null,
      checked_at: null,
      since: null,
    } as MergeStateInfo,
    has_external_checks: false,
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  } as AcceptCard
}

// 「去验收」是这张卡上唯一一个自己不干活的按钮（批准 / 采纳 / 退回 / 撤回都自己打
// API）：它要去的那个地方是右栏的「改动」那一格，而开在哪一格是 `TopicView` 的事。
// PanelCard 以前没接这个事件，于是点下去什么都不发生。这一条钉的是「点了要 emit 出
// 去」；透到哪一格由 PanelOverview.spec.ts 和 TopicView 那边管。
describe('卡上的「去验收」', () => {
  it('点下去把 review emit 出去，而不是自己找个地方去', async () => {
    getAcceptCards.mockResolvedValue({ data: [acceptCard()], total: 1 })
    const { emitted, getByText } = mount()
    await waitFor(() => getByText('去验收'))
    await fireEvent.click(getByText('去验收'))
    expect(emitted().review).toBeTruthy()
  })
})
