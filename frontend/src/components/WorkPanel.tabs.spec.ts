// 工作面板的 tab 栏：四格永远都在，打开房间时挑哪一格由房间所处的阶段说，但只挑
// 有东西可看的那一格。
//
// 这一份钉的是两件以前出过错的事：一格时有时无，同一个房间两次打开 tab 栏不一样
// 长；和待验收的房间没有改动文件时（项目还没接仓库），一进来就落在「改动」那一格
// 的报错上。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const workSummary = vi.fn()

vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: (...a: unknown[]) => workSummary(...a),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
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
      plugins: [vuetify],
      stubs: { PanelOverview: true, PanelSite: true, PanelChanges: true, PanelPreview: true },
    },
  })
}

function selected(container: Element): string {
  return container.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() ?? ''
}

describe('tab 栏', () => {
  it('一个还没跑过的房间也是四格，顺序不变', async () => {
    const { findAllByRole } = mount()
    const labels = (await findAllByRole('tab')).map((t) => t.textContent?.trim() ?? '')
    expect(labels).toEqual(['总览', '现场', '改动', '预览'])
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
    const { container } = mount({ phase: 'reviewing' })
    await waitFor(() => expect(selected(container)).toMatch(/^改动/))
  })

  it('待验收、但一个改动文件都没有：留在「总览」，不落在一格空的上', async () => {
    const { container, emitted } = mount({ phase: 'reviewing' })
    await waitFor(() => expect(workSummary).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(selected(container)).toBe('总览')
    expect(emitted()['update:tab']).toBeUndefined()
  })
})
