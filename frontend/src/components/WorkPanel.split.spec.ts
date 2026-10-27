// 工作面板并排看两格：挂在面板这一层验接线——页签条上那颗按钮、两格同时画出来、
// 右边那格排在后面。规则本身在 composables/usePanelSplit.spec.ts。
//
// happy-dom 没有布局，面板宽度量出来是 0（并排要 720 以上），这里把量宽那一步换成
// 一个够宽的数。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

vi.mock('@vueuse/core', async (orig) => ({
  ...(await orig<typeof import('@vueuse/core')>()),
  useElementSize: () => ({ width: ref(1200), height: ref(800), stop: () => {} }),
}))
vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: vi.fn(async () => ({ has_run: false, changed_files: [] })),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
}))
vi.mock('../api/routines', () => ({
  listProjectRoutines: vi.fn(async () => ({ data: [], total: 0 })),
  getRoutine: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
}))

import WorkPanel from './WorkPanel.vue'

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

function mount() {
  return render(WorkPanel as unknown as Component, {
    props: { topic, activityTick: 0 },
    slots: {
      overview: '<div data-testid="pane-overview">overview</div>',
      threads: '<div data-testid="pane-threads">threads</div>',
    },
    global: {
      plugins: [vuetify, i18n],
      stubs: {
        PanelSiteHost: true,
        PanelChangesHost: true,
        PanelPreviewHost: true,
        RoutinePanelHost: true,
        RoomFilesPanelHost: true,
      },
    },
  })
}

function visible(el: HTMLElement | null): boolean {
  return !!el && !(el.parentElement?.getAttribute('style') ?? '').includes('display: none')
}

it('看过另一格以后，页签条上能并排；两格同时画出来，上一格排在右边', async () => {
  const ui = mount()
  // 还只看过一格：没有可以并排的。
  expect(ui.queryByTestId('panel-split')).toBeNull()

  await fireEvent.click(await ui.findByRole('tab', { name: /支线/ }))
  const button = await ui.findByTestId('panel-split')
  await fireEvent.click(button)

  await waitFor(() => expect(visible(ui.getByTestId('pane-overview'))).toBe(true))
  expect(visible(ui.getByTestId('pane-threads'))).toBe(true)
  expect(ui.getByTestId('pane-overview').parentElement!.classList.contains('tabpane--side')).toBe(true)
  expect(button.getAttribute('aria-pressed')).toBe('true')
  // 页签条上看得出右边那一格也在屏幕上。
  expect(ui.getByRole('tab', { name: /概览/ }).classList.contains('tabbar__tab--side')).toBe(true)

  // 再按一次收回一列：只剩此刻这一格。
  await fireEvent.click(ui.getByTestId('panel-split'))
  await waitFor(() => expect(visible(ui.getByTestId('pane-overview'))).toBe(false))
  expect(visible(ui.getByTestId('pane-threads'))).toBe(true)
})

it('面板报出此刻开着哪一份文件（撰写框上「芝士会看到你在看什么」读它）', async () => {
  const ui = render(WorkPanel as unknown as Component, {
    props: { topic, activityTick: 0, tab: 'file:output/招募方案.md' },
    global: {
      plugins: [vuetify, i18n],
      stubs: {
        PanelSiteHost: true,
        PanelChangesHost: true,
        PanelPreviewHost: true,
        RoutinePanelHost: true,
        RoomFilesPanelHost: true,
      },
    },
  })
  await waitFor(() => expect((ui.emitted().viewing ?? []).at(-1)).toEqual(['output/招募方案.md']))
  await fireEvent.click(await ui.findByRole('tab', { name: /概览/ }))
  await waitFor(() => expect((ui.emitted().viewing ?? []).at(-1)).toEqual([null]))
})
