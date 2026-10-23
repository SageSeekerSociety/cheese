/**
 * 例行事务（项目总览）：存下来就在跑（没有「等人确认」这一步）；删一条规则之前要问一句；
 * 一次失败的执行说得出为什么。
 *
 * 行上的动作全在行尾那个 ⋯ 里（设计稿：行只留名字、什么时候跑、一颗状态图形），所以
 * 这里要动手的都先开那个菜单；状态是图形，读的是它给读屏的那句话。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectRoutinesView from './ProjectRoutinesView.vue'

import i18n, { setLocale } from '@/i18n'

vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))

// 规则那一半的取数在新模块里（`api.ts` 只能变短），页面自己还要用它查房间。
vi.mock('../api', () => ({
  listTopics: vi.fn(),
}))

vi.mock('../api/routines', () => ({
  listProjectRoutines: vi.fn(),
  getRoutine: vi.fn(),
  listRoutineRuns: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
  listRoutineTemplates: vi.fn(),
  createRoutineTemplate: vi.fn(),
  deleteRoutineTemplate: vi.fn(),
}))

const { listTopics } = await import('../api')
const { deleteRoutine, getRoutine, listProjectRoutines, listRoutineRuns, listRoutineTemplates, routineAction } =
  await import('../api/routines')

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
  // 行尾那个 ⋯ 是 VOverlay，定位要读这个，happy-dom 没有。
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

const base = {
  can_delete: true,
  room_archived: false,
  project_id: 'p1',
  topic_id: 'room-1',
  instructions: '整理本周进展',
  note: '',
  trigger: 'schedule' as const,
  trigger_text: '每周周一 09:00（Asia/Shanghai）',
  // 后端把「什么时候跑」那句话的键和参数也一起回：切语言时那一行跟着换，用的是键，
  // 不是它写下的那句中文（见 `routineWhenLine`）。
  i18n: {
    trigger_text: {
      key: 'routineTriggerWeekly',
      params: {
        days: { list: [{ key: 'routineWeekdayMon', params: {} }], quoted: false },
        time: '09:00',
        timezone: 'Asia/Shanghai',
      },
    },
  },
  spec: { freq: 'weekly', weekdays: [0], time: '09:00' },
  timezone: 'Asia/Shanghai',
  last_run_status: 'succeeded' as const,
  credits_this_month: 42.5,
  agent_handle: 'cheese-x',
  owner_handle: 'u1',
  created_by: 'cheese-x',
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
      { ...base, id: 'live-1', title: '已经在跑的日报', state: 'active' },
      { ...base, id: 'live-2', title: '暂停了的周报', state: 'paused' },
    ],
    total: 2,
  })
  vi.mocked(listRoutineRuns).mockResolvedValue({ data: [], total: 0, credits_this_month: 0 })
  // 模板存在项目里（`api/routines.ts` 那三个接口），默认这个项目一条也没有。
  vi.mocked(listRoutineTemplates).mockResolvedValue({ data: [], total: 0 })
})

function mount() {
  return render(ProjectRoutinesView, {
    props: { projectId: 'p1' },
    global: { plugins: [vuetify, i18n], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
}

function buttonIn(scope: Element, label: string): HTMLElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

function row(container: Element, id: string): Element {
  return container.querySelector(`[data-routine="${id}"]`)!
}

/** 行尾 ⋯ 里此刻有哪几件。 */
async function openMenu(scope: Element): Promise<string[]> {
  await fireEvent.click(scope.querySelector<HTMLButtonElement>('button[aria-label="更多操作"]')!)
  await waitFor(() => expect(document.querySelector('.v-overlay .v-list-item-title')).toBeTruthy())
  return Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map(
    (el) => el.textContent?.trim() as string
  )
}

/** 点开 ⋯ 里写着这几个字的那一件。 */
async function choose(scope: Element, label: string) {
  await openMenu(scope)
  await fireEvent.click(
    Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).find(
      (el) => el.textContent?.trim() === label
    ) as HTMLElement
  )
}

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))

describe('例行事务', () => {
  it('reads in English under the en locale', async () => {
    setLocale('en')
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('已经在跑的日报'))
    expect(container.textContent).toContain('Every Monday at 09:00 (Beijing time)')
    expect(row(container, 'live-2').querySelector('.routine-row__state')?.getAttribute('aria-label')).toBe('Paused')
  })

  // 存下来就在跑：暂停 / 恢复就在 ⋯ 里，没有「等人确认」那一步。
  it('存下来就在跑：没有「确认启用」，暂停发的是「暂停」', async () => {
    vi.mocked(routineAction).mockResolvedValue({ ...base, id: 'live-1', title: '已经在跑的日报', state: 'paused' })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('已经在跑的日报'))

    expect(container.textContent).not.toContain('确认启用')
    expect(container.textContent).not.toContain('等你确认')
    expect(await openMenu(row(container, 'live-2'))).toContain('恢复')
    expect(await openMenu(row(container, 'live-2'))).not.toContain('暂停')

    await choose(row(container, 'live-1'), '暂停')

    expect(routineAction).toHaveBeenCalledWith('live-1', 'pause')
  })

  it('取消删除，规则还在，也没有请求删除', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('已经在跑的日报'))

    await choose(row(container, 'live-1'), '删除')
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

    await choose(row(container, 'live-1'), '执行记录')

    await waitFor(() => expect(container.textContent).toContain('失败'))
    // 那一行够长了：原因收着，点「原因」才摊开。
    expect(container.textContent).not.toContain('没有交回结果')
    await fireEvent.click(container.querySelector('.routine-run__why')!)
    expect(container.textContent).toContain('没有交回结果')
  })

  // 行里只摊最近三次。第四条往后的在整页里 —— 那一页是这一格里的另一个视图，不是新路由。
  it('「看全部」：这一页换成整页记录，返回回到这一串', async () => {
    vi.mocked(getRoutine).mockResolvedValue({
      ...base,
      id: 'live-1',
      title: '已经在跑的日报',
      state: 'active',
      runs: Array.from({ length: 4 }, (_, i) => ({
        id: `run-${i}`,
        routine_id: 'live-1',
        trigger_detail: '',
        routine_revision: 1,
        scheduled_for: '2026-09-28T01:00:00Z',
        status: 'succeeded' as const,
        summary: '已汇总',
        outputs: [],
        error: '',
        created_at: '2026-09-28T01:00:00Z',
        started_at: null,
        finished_at: '2026-09-28T01:10:00Z',
      })),
    })
    vi.mocked(listRoutineRuns).mockResolvedValue({
      data: [],
      total: 34,
      credits_this_month: 0,
    })
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('已经在跑的日报'))

    await choose(row(container, 'live-1'), '执行记录')
    await waitFor(() => expect(container.querySelector('.routine-runs__all')).toBeTruthy())
    await fireEvent.click(container.querySelector<HTMLButtonElement>('.routine-runs__all')!)

    await waitFor(() => expect(container.textContent).toContain('已经在跑的日报 · 执行记录'))
    expect(container.textContent).toContain('共 34 次')
    expect(listRoutineRuns).toHaveBeenCalledWith('live-1', { offset: 0, limit: 10, failedOnly: false })

    await fireEvent.click(container.querySelector<HTMLButtonElement>('button[aria-label="返回"]')!)
    await waitFor(() => expect(container.querySelector('.routine-list')).toBeTruthy())
    expect(container.querySelector('.records')).toBeNull()
  })
})
