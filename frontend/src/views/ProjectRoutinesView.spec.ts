/**
 * 定时与触发：芝士起草的规则要人确认才跑；删一条规则之前要问一句；一次失败的执行
 * 说得出为什么。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectRoutinesView from './ProjectRoutinesView.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))

vi.mock('../api', () => ({
  listProjectRoutines: vi.fn(),
  listTopics: vi.fn(),
  getRoutine: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
}))

const { deleteRoutine, getRoutine, listProjectRoutines, listTopics, routineAction } = await import('../api')

const vuetify = createVuetify({ components, directives })

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

const base = {
  project_id: 'p1',
  topic_id: 'room-1',
  instructions: '整理本周进展',
  context_scope: '',
  output_dir: '周报',
  trigger: 'schedule' as const,
  trigger_text: '每周周一 09:00（Asia/Shanghai）',
  spec: { freq: 'weekly', weekdays: [0], time: '09:00' },
  timezone: 'Asia/Shanghai',
  agent_handle: 'cheese-x',
  owner_handle: 'u1',
  proposed_by: 'cheese-x',
  confirmed_by: null,
  confirmed_at: null,
  next_run_at: null,
  revision: 1,
  created_at: '2026-09-25T00:00:00Z',
  updated_at: '2026-09-25T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listTopics).mockResolvedValue({
    data: [{ id: 'room-1', title: '周报房间', status: 'active' }],
    total: 1,
  } as never)
  vi.mocked(listProjectRoutines).mockResolvedValue({
    data: [
      { ...base, id: 'draft-1', title: '芝士起草的周报', state: 'draft' },
      { ...base, id: 'live-1', title: '已经在跑的日报', state: 'active', next_run_at: '2026-09-28T01:00:00Z' },
    ],
    total: 2,
  })
})

function mount() {
  return render(ProjectRoutinesView, {
    props: { projectId: 'p1' },
    global: { plugins: [vuetify], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
}

function buttonIn(scope: Element, label: string): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

function row(container: Element, id: string): Element {
  return container.querySelector(`[data-routine="${id}"]`)!
}

describe('定时与触发', () => {
  it('芝士起草的规则单独列在「等你确认」里，确认之后才算启用', async () => {
    vi.mocked(routineAction).mockResolvedValue({ ...base, id: 'draft-1', title: '芝士起草的周报', state: 'active' })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('等你确认'))

    expect(buttonIn(row(container, 'live-1'), '确认启用')).toBeUndefined()
    await fireEvent.click(buttonIn(row(container, 'draft-1'), '确认启用')!)

    expect(routineAction).toHaveBeenCalledWith('draft-1', 'confirm')
    await waitFor(() => expect(container.textContent).not.toContain('等你确认'))
  })

  it('取消删除，规则还在，也没有请求删除', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('已经在跑的日报'))

    await fireEvent.click(buttonIn(row(container, 'live-1'), '删除')!)
    await waitFor(() => expect(buttonIn(document.body, '取消')).toBeTruthy())
    await fireEvent.click(buttonIn(document.body, '取消')!)

    expect(deleteRoutine).not.toHaveBeenCalled()
    expect(container.textContent).toContain('已经在跑的日报')
  })

  it('一次失败的执行把原因摆出来', async () => {
    vi.mocked(getRoutine).mockResolvedValue({
      ...base,
      id: 'live-1',
      title: '已经在跑的日报',
      state: 'active',
      runs: [
        {
          id: 'run-1',
          routine_id: 'live-1',
          trigger_detail: '计划时间 2026-09-28 09:00。',
          routine_revision: 1,
          scheduled_for: '2026-09-28T01:00:00Z',
          status: 'failed',
          summary: '',
          outputs: [],
          error: 'AI 队友这一轮已经结束，但没有交回结果',
          created_at: '2026-09-28T01:00:00Z',
          started_at: null,
          finished_at: '2026-09-28T01:10:00Z',
        },
      ],
    })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('已经在跑的日报'))

    await fireEvent.click(buttonIn(row(container, 'live-1'), '执行记录')!)

    await waitFor(() => expect(container.textContent).toContain('没有交回结果'))
    expect(container.textContent).toContain('失败')
  })
})
