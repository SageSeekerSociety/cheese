// 房间右侧「例行事务」那一格的取数外壳：**只问这一个房间**。
//
// 它和项目「例行事务」页画的是同一串（`RoutineBoard`，经 `panels/PanelRoutines.vue`）、
// 填的是同一张表，差别只有范围：那一页是整个项目，这一格带着 `topicId` 去问后端。所以
// 这一份钉的是这件只有这一格才成立的事：问的是这个房间、新建的规则长在这个房间（表单
// 里连「在哪个房间执行」都不问）。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

vi.mock('../../api/routines', () => ({
  listProjectRoutines: vi.fn(),
  getRoutine: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
}))

import RoutinePanelHost from './RoutinePanelHost.vue'

const { createRoutine, listProjectRoutines, updateRoutine } = await import('../../api/routines')

const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))

afterEach(cleanup)

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

const live = {
  id: 'live-1',
  can_manage: true,
  room_archived: false,
  project_id: 'p1',
  topic_id: 'room-1',
  title: '这个房间的日报',
  instructions: '把这个房间今天的进展写成一页',
  output_dir: '日报',
  trigger: 'schedule' as const,
  trigger_text: '每天 09:00（Asia/Shanghai）',
  spec: { freq: 'daily', time: '09:00' },
  scope_topic_ids: [],
  scope_topics: [],
  last_run: null,
  run_count: 0,
  timezone: 'Asia/Shanghai',
  state: 'active' as const,
  agent_handle: 'cheese-x',
  owner_handle: 'u1',
  proposed_by: 'cheese-x',
  confirmed_by: 'u1',
  confirmed_at: '2026-09-25T02:00:00Z',
  next_run_at: '2026-09-28T01:00:00Z',
  revision: 1,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listProjectRoutines).mockResolvedValue({ data: [live], total: 1 })
})

function mount() {
  return render(RoutinePanelHost, {
    props: { topicId: 'room-1', topicTitle: '这个房间的日报', projectId: 'p1' },
    global: { plugins: [vuetify, i18n] },
  })
}

function button(label: string, scope: ParentNode = document.body): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

/** 此刻弹出来的菜单里写着这个字的那一行。 */
function menuItem(label: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll<HTMLElement>('.v-overlay--active .v-list-item-title')).find(
    (el) => el.textContent?.trim() === label
  )
}

/** 已经跑起来的那条，动作都在行尾那颗 ⋯ 里：点开它。 */
async function openRowMenu(rowEl: Element, label: string) {
  await fireEvent.click(rowEl.querySelector('button')!)
  await waitFor(() => expect(menuItem(label)).toBeTruthy())
}

/** 点开 ⋯ 再点要的那一样。 */
async function rowAction(rowEl: Element, label: string) {
  await openRowMenu(rowEl, label)
  await fireEvent.click(menuItem(label)!.closest('.v-list-item') ?? menuItem(label)!)
}

describe('房间右侧的「例行事务」', () => {
  it('问的是这一个房间，不是整个项目', async () => {
    const { findByText } = mount()
    await findByText('这个房间的日报')
    expect(listProjectRoutines).toHaveBeenCalledWith('p1', 'room-1')
  })

  it('还没有规则：写的是「点新建」，不是一个「暂无」', async () => {
    vi.mocked(listProjectRoutines).mockResolvedValue({ data: [], total: 0 })
    const { findByText } = mount()
    await findByText('还没有例行事务')
  })

  // 频道里的人都能暂停、能立即执行一次，所以这一行照样有 ⋯；只有确认启用和删除是负责
  // 人和管理员的事，做不到就不出现，也不写一句解释。
  it('别人的规则：能做的照样有，做不到的不出现', async () => {
    vi.mocked(listProjectRoutines).mockResolvedValue({
      data: [{ ...live, can_manage: false, owner_handle: 'u-other' }],
      total: 1,
    })
    const { container, findByText } = mount()
    await findByText('这个房间的日报')
    const row = container.querySelector('[data-routine="live-1"]')!
    expect(row.textContent).not.toContain('只有他和项目管理员能改')
    await openRowMenu(row, '暂停')
    expect(menuItem('暂停')).toBeTruthy()
    expect(menuItem('修改')).toBeTruthy()
    expect(menuItem('删除')).toBeUndefined()
  })

  // 新建只发那一段大白话：「每周什么时候跑」不是人选的，是 AI 从这段话里起草出来的，
  // 之后摊在「待确认」那一条里给他改。所以这里连执行条件那一格都没有。
  it('在这一格里新建：只有一段大白话，房间是定死的', async () => {
    vi.mocked(createRoutine).mockResolvedValue({ ...live, id: 'new-1', state: 'draft' })
    const { findByText } = mount()
    await findByText('这个房间的日报')

    await fireEvent.click(button('新建')!)
    await waitFor(() => expect(button('提交')).toBeTruthy())
    expect(document.body.textContent).not.toContain('在哪个房间执行')
    expect(document.body.textContent).toContain('在「这个房间的日报」这个频道执行')
    expect(document.body.textContent).not.toContain('执行范围')

    const brief = document.querySelector('.v-dialog textarea')!
    await fireEvent.update(brief, '每天下班前把这个房间今天的进展写成一页')
    await fireEvent.click(button('提交')!)

    await waitFor(() => expect(createRoutine).toHaveBeenCalled())
    const [room, body] = vi.mocked(createRoutine).mock.calls[0]
    expect(room).toBe('room-1')
    expect(body.trigger).toBe('schedule')
    expect(body.spec).toEqual({})
    expect(body.instructions).toBe('每天下班前把这个房间今天的进展写成一页')
  })

  it('改一条每日分诊的钟点：它附带的反馈条数跟着留下，不被表单丢掉', async () => {
    const triage = { ...live, spec: { freq: 'daily', time: '09:00', feedback_batch: 5 } }
    vi.mocked(listProjectRoutines).mockResolvedValue({ data: [triage], total: 1 })
    vi.mocked(updateRoutine).mockResolvedValue(triage)
    const { findByText } = mount()
    await findByText('这个房间的日报')

    const row = document.querySelector('[data-routine="live-1"]')!
    await rowAction(row, '修改')
    await waitFor(() => expect(button('保存')).toBeTruthy())
    await fireEvent.click(button('保存')!)

    await waitFor(() => expect(updateRoutine).toHaveBeenCalled())
    const body = vi.mocked(updateRoutine).mock.calls[0][1]
    expect(body.spec).toEqual({ freq: 'daily', time: '09:00', feedback_batch: 5 })
  })
})
