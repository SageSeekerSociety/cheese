// 手机上一屏放不下「对话」和「工作面板」两栏，所以对话是这条 tab 栏的第一格。
//
// 这一份守的是那一格的存在和落点：开着 withChat 时它在、而且没有别的段特别要看
// 的时候就停在它上面（你进话题多半是来说话的）；桌面上关着 withChat，对话在旁边
// 那一栏，这一格不该出现——多一格空 tab 比少一格更难解释。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: vi.fn(async () => ({ has_run: false, changed_files: [] })),
  // 房间自己的文件（人传上来的那份预览）：`openFile` 先问它认不认这个路径，认了就在
  // 自由区开一格，不去项目版本那一格。
  readPreviewFile: vi.fn(async () => ({})),
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
      stubs: { PanelDocHost: true, PanelSiteHost: true, PanelChangesHost: true, PanelPreviewHost: true },
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
    expect(queryByRole('tab', { name: /概览/ })).toBeTruthy()
    expect(queryByRole('tab', { name: /对话/ })).toBeNull()
  })

  // 芝士正在干活时桌面上的任务会自动开在现场；手机上那样做等于把输入框藏起来。
  // 现场只长在任务上，所以这两条挂的是一个任务。
  it('手机上芝士正在工作，任务仍开在对话', async () => {
    const { findByRole } = mount({ taskId: 'task-1', withChat: true, cardPhase: null, working: true })
    await new Promise((r) => setTimeout(r, 0))
    expect((await findByRole('tab', { name: /对话/ })).getAttribute('aria-selected')).toBe('true')
    expect((await findByRole('tab', { name: /现场/ })).getAttribute('aria-selected')).toBe('false')
  })

  it('桌面上芝士正在工作，任务开在现场', async () => {
    const { findByRole } = mount({ taskId: 'task-1', withChat: false, cardPhase: null, working: true })
    await new Promise((r) => setTimeout(r, 0))
    expect((await findByRole('tab', { name: /现场/ })).getAttribute('aria-selected')).toBe('true')
  })

  it('手机上退回到不带页签的地址，回到对话', async () => {
    const { findByRole, rerender } = mount({ taskId: 'task-1', withChat: true, tab: 'changes' })
    expect((await findByRole('tab', { name: /改动/ })).getAttribute('aria-selected')).toBe('true')
    await rerender({ tab: undefined })
    expect((await findByRole('tab', { name: /对话/ })).getAttribute('aria-selected')).toBe('true')
  })
})

// 点开一份预览、再关掉：落回你点它之前待着的格子，不是「概览」。真机反馈
// 2026-10-12：手机上关掉预览落到「概览」，得再点一下才能回到正在说的话里。
describe('关掉自由区最后一份，回到来处', () => {
  const flush = () => new Promise((r) => setTimeout(r, 0))

  function selectedOf(container: Element): string {
    return container.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() ?? ''
  }

  function mountOpenable(props: Record<string, unknown>) {
    const panel = ref<{ openFile: (path: string) => Promise<void> } | null>(null)
    const Host = defineComponent(() => () => h(Panel, { ref: panel, topic, activityTick: 0, ...props }))
    const ui = render(Host, {
      slots: { chat: '<div data-testid="chat-pane">对话内容</div>' },
      global: {
        plugins: [vuetify, i18n],
        stubs: { PanelDocHost: true, PanelSiteHost: true, PanelChangesHost: true, PanelPreviewHost: true },
      },
    })
    return { ...ui, panel }
  }

  async function openPreview(panel: { value: { openFile: (p: string) => Promise<void> } | null }) {
    await panel.value!.openFile('library/repro.html')
    await flush()
  }

  it('手机：从对话点开的，关掉回到对话', async () => {
    const { container, panel } = mountOpenable({ withChat: true })
    await flush()
    await openPreview(panel)
    expect(selectedOf(container)).toContain('repro.html')

    await fireEvent.click(container.querySelector('.tabbar__close')!)
    await flush()

    expect(selectedOf(container)).toContain('对话')
  })

  it('桌面：从概览点开的，关掉回到概览', async () => {
    const { container, panel } = mountOpenable({ withChat: false })
    await flush()
    await openPreview(panel)

    await fireEvent.click(container.querySelector('.tabbar__close')!)
    await flush()

    expect(selectedOf(container)).toContain('概览')
  })

  it('开着两份：关掉正看着的那份，落到旁边那份，不来处', async () => {
    const { container, panel } = mountOpenable({ withChat: true })
    await flush()
    await openPreview(panel)
    // 钉住第一份：不钉的话第二份会占掉它那个临时位（自由区只有一格临时位）。
    await fireEvent.dblClick(container.querySelector('.tabbar__file .tabbar__tab')!)
    await flush()
    await panel.value!.openFile('library/second.html')
    await flush()
    expect(selectedOf(container)).toContain('second.html')

    const close = [...container.querySelectorAll('.tabbar__close')].find((b) =>
      b.getAttribute('aria-label')?.includes('second.html')
    )!
    await fireEvent.click(close)
    await flush()

    expect(selectedOf(container)).toContain('repro.html')
  })
})
