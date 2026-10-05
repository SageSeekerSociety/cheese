// 房间右侧「定时与触发」那一格的取数外壳：**只问这一个房间**。
//
// 它和项目「定时与触发」页画的是同一串（`RoutineBoard`，经 `panels/PanelRoutines.vue`）、
// 填的是同一张表，差别只有范围：那一页是整个项目，这一格带着 `topicId` 去问后端。所以
// 这一份钉的是三件只有这一格才成立的事：问的是这个房间、新建的规则长在这个房间（表单
// 里连「在哪个房间执行」都不问）、拿不到权限的规则只读。
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
  context_scope: '',
  output_dir: '日报',
  trigger: 'schedule' as const,
  trigger_text: '每天 09:00（Asia/Shanghai）',
  spec: { freq: 'daily', time: '09:00' },
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
    props: { topicId: 'room-1', projectId: 'p1' },
    global: { plugins: [vuetify, i18n] },
  })
}

function button(label: string, scope: ParentNode = document.body): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

describe('房间右侧的「定时与触发」', () => {
  it('问的是这一个房间，不是整个项目', async () => {
    const { findByText } = mount()
    await findByText('这个房间的日报')
    expect(listProjectRoutines).toHaveBeenCalledWith('p1', 'room-1')
  })

  it('还没有规则：写的是「点新建」，不是一个「暂无」', async () => {
    vi.mocked(listProjectRoutines).mockResolvedValue({ data: [], total: 0 })
    const { findByText } = mount()
    await findByText('还没有定时或触发规则')
  })

  it('别人的规则：只读，一颗按钮都不画', async () => {
    vi.mocked(listProjectRoutines).mockResolvedValue({
      data: [{ ...live, can_manage: false, owner_handle: 'u-other' }],
      total: 1,
    })
    const { container, findByText } = mount()
    await findByText('这个房间的日报')
    const row = container.querySelector('[data-routine="live-1"]')!
    expect(row.querySelectorAll('button')).toHaveLength(0)
    expect(row.textContent).toContain('只有他和项目管理员能改')
  })

  it('在这一格里新建：房间是定死的，不问「在哪个房间执行」', async () => {
    vi.mocked(createRoutine).mockResolvedValue({ ...live, id: 'new-1', state: 'draft' })
    const { findByText } = mount()
    await findByText('这个房间的日报')

    await fireEvent.click(button('新建')!)
    await waitFor(() => expect(button('保存')).toBeTruthy())
    expect(document.body.textContent).not.toContain('在哪个房间执行')

    await fireEvent.click(button('保存')!)

    await waitFor(() => expect(createRoutine).toHaveBeenCalled())
    const [room, body] = vi.mocked(createRoutine).mock.calls[0]
    expect(room).toBe('room-1')
    expect(body.trigger).toBe('schedule')
    expect(body.spec).toEqual({ freq: 'weekly', time: '09:00', weekdays: [0] })
  })

  it('改一条每日分诊的钟点：它附带的反馈条数跟着留下，不被表单丢掉', async () => {
    const triage = { ...live, spec: { freq: 'daily', time: '09:00', feedback_batch: 5 } }
    vi.mocked(listProjectRoutines).mockResolvedValue({ data: [triage], total: 1 })
    vi.mocked(updateRoutine).mockResolvedValue(triage)
    const { findByText } = mount()
    await findByText('这个房间的日报')

    const row = document.querySelector('[data-routine="live-1"]')!
    await fireEvent.click(button('修改', row)!)
    await waitFor(() => expect(button('保存')).toBeTruthy())
    await fireEvent.click(button('保存')!)

    await waitFor(() => expect(updateRoutine).toHaveBeenCalled())
    const body = vi.mocked(updateRoutine).mock.calls[0][1]
    expect(body.spec).toEqual({ freq: 'daily', time: '09:00', feedback_batch: 5 })
  })
})
