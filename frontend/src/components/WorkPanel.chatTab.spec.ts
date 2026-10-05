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

import i18n, { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

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
      plugins: [vuetify, i18n],
      // 四个子面板各自会去拿数据/建编辑器，这一份只关心 tab 栏本身。
      stubs: { PanelDocHost: true, PanelSite: true, PanelChangesHost: true, PanelPreviewHost: true },
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
    const { queryByRole } = mount({ withChat: false })
    expect(queryByRole('tab', { name: /现场/ })).toBeTruthy()
    expect(queryByRole('tab', { name: /对话/ })).toBeNull()
  })

  // 芝士正在干活时桌面会自动开在现场；手机上那样做等于把输入框藏起来。
  it('手机上芝士正在工作，房间仍开在对话', async () => {
    const { findByRole } = mount({ withChat: true, cardPhase: null, working: true })
    await new Promise((r) => setTimeout(r, 0))
    expect((await findByRole('tab', { name: /对话/ })).getAttribute('aria-selected')).toBe('true')
    expect((await findByRole('tab', { name: /现场/ })).getAttribute('aria-selected')).toBe('false')
  })

  it('桌面上芝士正在工作，房间开在现场', async () => {
    const { findByRole } = mount({ withChat: false, cardPhase: null, working: true })
    await new Promise((r) => setTimeout(r, 0))
    expect((await findByRole('tab', { name: /现场/ })).getAttribute('aria-selected')).toBe('true')
  })

  it('手机上退回到不带页签的地址，回到对话', async () => {
    const { findByRole, rerender } = mount({ withChat: true, tab: 'changes' })
    expect((await findByRole('tab', { name: /改动/ })).getAttribute('aria-selected')).toBe('true')
    await rerender({ tab: undefined })
    expect((await findByRole('tab', { name: /对话/ })).getAttribute('aria-selected')).toBe('true')
  })
})
