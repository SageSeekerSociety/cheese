// 手机上一屏放不下「对话」和「工作面板」两栏，所以对话是这条 tab 栏的第一格。
//
// 这一份守的是那一格的存在和落点：开着 withChat 时它在、而且没有别的段特别要看
// 的时候就停在它上面（你进话题多半是来说话的）；桌面上关着 withChat，对话在旁边
// 那一栏，这一格不该出现——多一格空 tab 比少一格更难解释。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: vi.fn(async () => ({ has_run: false, changed_files: [] })),
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
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({}), text: async () => '' }))
})

function mount(props: Record<string, unknown>) {
  return render(Panel, {
    props: { topic, activityTick: 0, ...props },
    slots: { chat: '<div data-testid="chat-pane">对话内容</div>' },
    global: {
      plugins: [vuetify],
      // 四个子面板各自会去拿数据/建编辑器，这一份只关心 tab 栏本身。
      stubs: { PanelDoc: true, PanelSite: true, PanelChanges: true, PanelPreview: true },
    },
  })
}

describe('对话作为工作面板的一格', () => {
  it('手机上有这一格，而且开在它上面', async () => {
    const { findByRole, getByTestId } = mount({ withChat: true })
    const tab = await findByRole('tab', { name: /对话/ })
    expect(tab.getAttribute('aria-selected')).toBe('true')
    expect(getByTestId('chat-pane')).toBeTruthy()
  })

  it('对话排在文档前面', async () => {
    const { findAllByRole } = mount({ withChat: true })
    const labels = (await findAllByRole('tab')).map((t) => t.textContent?.trim() ?? '')
    expect(labels[0]).toContain('对话')
  })

  it('桌面上没有这一格', async () => {
    const { queryByRole } = mount({ withChat: false, working: true })
    // 有两格以上 tab 栏才出现，所以给一个「现场」让它显形，再确认没有「对话」。
    expect(queryByRole('tab', { name: /现场/ })).toBeTruthy()
    expect(queryByRole('tab', { name: /对话/ })).toBeNull()
  })
})
