// 工作面板把「能不能写评论」递给预览那一格：归档的频道一律不能（和改动那一格的只读同一个
// 判据），没归档时听页面按项目权限算的那一句。以前这一格什么都不收，归档频道里照样能写。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
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

import * as api from '../api'
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

/** 预览那一格换成只记下收到什么的桩。频道里的预览是自由区里一份文件的页签（地址点名就开）。 */
const PreviewProbe = defineComponent({
  props: { canComment: { type: Boolean, default: undefined } },
  setup: (props) => () => h('div', { 'data-testid': 'preview', 'data-can-comment': String(props.canComment) }),
})

function mount(props: Record<string, unknown>) {
  return render(WorkPanel as unknown as Component, {
    props: { topic, activityTick: 0, tab: 'file:output/说明.md', ...props },
    global: {
      plugins: [vuetify, i18n],
      stubs: {
        PanelSiteHost: true,
        PanelChangesHost: true,
        PanelPreviewHost: PreviewProbe,
        RoutinePanelHost: true,
        RoomFilesPanelHost: true,
      },
    },
  })
}

it('正常频道、页面说能写：预览那一格能写评论', async () => {
  const ui = mount({ canComment: true })
  expect((await ui.findByTestId('preview')).dataset.canComment).toBe('true')
})

it('页面说不能写（角色没有「评论」、项目归档）：预览那一格不能写', async () => {
  const ui = mount({ canComment: false })
  expect((await ui.findByTestId('preview')).dataset.canComment).toBe('false')
})

it('归档的频道：页面漏传也不能写', async () => {
  const ui = mount({ topic: { ...topic, status: 'archived' } })
  expect((await ui.findByTestId('preview')).dataset.canComment).toBe('false')
})

it('任务页上「预览」那一格（芝士最后摆出来的那一样）：归档频道里同样不能写', async () => {
  vi.mocked(api.getPreview).mockResolvedValue({ artifact_id: 'a1', path: 'output/说明.md' } as never)
  const ui = mount({ topic: { ...topic, status: 'archived' }, taskId: 'k1', tab: 'preview', canComment: true })
  expect((await ui.findByTestId('preview', {}, { timeout: 3000 })).dataset.canComment).toBe('false')
})
