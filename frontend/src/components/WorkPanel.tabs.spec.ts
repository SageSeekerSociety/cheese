// 工作面板的 tab 栏：那几格永远都在，打开房间时挑哪一格由房间所处的阶段说，但只挑
// 有东西可看的那一格。
//
// 这一份钉的是两件以前出过错的事：一格时有时无，同一个房间两次打开 tab 栏不一样
// 长；和待验收的房间没有改动文件时（项目还没接仓库），一进来就落在「改动」那一格
// 的报错上。
//
// 第五格「定时与触发」和另外四格的来路不同（它由 `workPanelTabs` 接在共用表后面，
// 不在那条栏和文档演示共用的表里），所以它是不是真在这条栏上、地址点名它时能不能
// 打开，也在这里钉一次。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

const workSummary = vi.fn()

vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: (...a: unknown[]) => workSummary(...a),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
}))

// 这一格在下面被 stub 掉所以不渲染，但模块还是要被求值一次；不 mock 它的话，它会
// 从上面那个假 `../api` 里找 `request`，找不到。
vi.mock('../api/routines', () => ({
  listProjectRoutines: vi.fn(async () => ({ data: [], total: 0 })),
  getRoutine: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
}))

import WorkPanel from './WorkPanel.vue'

const Panel = WorkPanel as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  workSummary.mockResolvedValue({ has_run: false, changed_files: [] })
})

function mount(props: Record<string, unknown> = {}) {
  return render(Panel, {
    props: { topic, activityTick: 0, ...props },
    global: {
      plugins: [vuetify, i18n],
      stubs: {
        PanelOverviewHost: true,
        PanelSiteHost: true,
        PanelChangesHost: true,
        PanelPreviewHost: true,
        RoutinePanelHost: true,
      },
    },
  })
}

function selected(container: Element): string {
  return container.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() ?? ''
}

describe('tab 栏', () => {
  it('一个还没跑过的房间也是这几格，顺序不变', async () => {
    const { findAllByRole } = mount()
    const labels = (await findAllByRole('tab')).map((t) => t.textContent?.trim() ?? '')
    expect(labels).toEqual(['总览', '支线', '现场', '改动', '预览', '定时与触发'])
  })

  it('任务里没有支线这一格：支线只挂在频道主线的消息下面', async () => {
    const { findAllByRole } = mount({ taskId: 'task1' })
    const labels = (await findAllByRole('tab')).map((t) => t.textContent?.trim() ?? '')
    expect(labels).not.toContain('支线')
  })

  // 「定时与触发」永远有得看：没有规则时那一格是「还没有规则，点新建」，不是一个
  // 「暂无」。所以它不跟着 summary 变浅。
  it('「定时与触发」不是一格空的', async () => {
    const { findByRole } = mount()
    const routines = await findByRole('tab', { name: /定时与触发/ })
    expect(routines.classList.contains('tabbar__tab--empty')).toBe(false)
  })

  it('地址点名「定时与触发」：开在那一格', async () => {
    const { container } = mount({ tab: 'routines' })
    await waitFor(() => expect(selected(container)).toBe('定时与触发'))
  })

  it('没东西的那一格字是浅的，有东西的不是', async () => {
    workSummary.mockResolvedValue({ has_run: true, changed_files: [] })
    const { findByRole } = mount()
    const site = await findByRole('tab', { name: /现场/ })
    const changes = await findByRole('tab', { name: /改动/ })
    await waitFor(() => expect(site.classList.contains('tabbar__tab--empty')).toBe(false))
    expect(changes.classList.contains('tabbar__tab--empty')).toBe(true)
  })
})

describe('打开房间时开在哪一格', () => {
  it('待验收、而且真有改动：开在「改动」', async () => {
    workSummary.mockResolvedValue({ has_run: true, changed_files: ['a.ts'] })
    const { container } = mount({ cardPhase: 'pending' })
    await waitFor(() => expect(selected(container)).toMatch(/^改动/))
  })

  it('待验收、但一个改动文件都没有：留在「总览」，不落在一格空的上', async () => {
    const { container, emitted } = mount({ cardPhase: 'pending' })
    await waitFor(() => expect(workSummary).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(selected(container)).toBe('总览')
    expect(emitted()['update:tab']).toBeUndefined()
  })
})

// 平板横放（960–1180）：进房间时自动挑中的那一格，只是「你打开面板时看哪一格」，不能
// 写进地址——地址里一有 ?tab，那一档的浮层就跟着被拉起来了，而这一刻并没有人打开它。
// 宽档里没有浮层，地址照走（面板和地址永远一致）。
describe('平板横放：进房间不自动把面板拉起来', () => {
  it('芝士在干活：选中的是「现场」，但不告诉地址', async () => {
    const { container, emitted } = mount({ compact: true, working: true, cardPhase: null })
    await waitFor(() => expect(selected(container)).toBe('现场'))
    expect(emitted()['update:tab']).toBeUndefined()
  })

  it('待验收、真有改动：选中的是「改动」，同样不告诉地址', async () => {
    workSummary.mockResolvedValue({ has_run: true, changed_files: ['a.ts'] })
    const { container, emitted } = mount({ compact: true, cardPhase: 'pending' })
    await waitFor(() => expect(selected(container)).toMatch(/^改动/))
    expect(emitted()['update:tab']).toBeUndefined()
  })

  it('宽档里同一个情形照旧告诉地址，面板和地址一致', async () => {
    const { emitted } = mount({ working: true, cardPhase: null })
    await waitFor(() => expect(emitted()['update:tab']).toContainEqual(['site']))
  })
})
