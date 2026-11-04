import type { Block } from '../../cx_types'
import type { RecordPart } from '../../lib/sessionRecords'

import { defineComponent, h } from 'vue'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { usePanelSite } from '../../composables/usePanelSite'
import { storageRemove } from '../../lib/browserStorage'
import { forgetWindows } from '../../lib/siteWindow'

import PanelSite from './PanelSite.vue'

const api = vi.hoisted(() => ({ getTranscript: vi.fn(), sessionRecordImageUrl: vi.fn() }))
vi.mock('../../api', () => ({ ...api, SITE_PAGE_SIZE: 120 }))
vi.mock('../../composables/useSessionInspector', () => ({ useSessionInspector: () => ({}) }))
// 会话栏在这里只当一格占位：概览／原始记录的切换放在它的 actions 插槽里，
// 所以替身要把插槽画出来，不然这个 spec 点不到那两个按钮。
vi.mock('../SessionInspector.vue', () => ({ default: { template: '<div><slot name="actions" /></div>' } }))
// 空状态是 Vuetify 的那一只；这一个 spec 没装 Vuetify，只关心那句字出没出来。
vi.mock('@/components/base/BaseEmptyState.vue', () => ({
  default: { props: ['title'], template: '<div class="empty-stub">{{ title }}</div>' },
}))
vi.mock('@/i18n', () => ({
  t: (key: string) =>
    ({
      'work.room.site.overview': 'Overview',
      'work.room.site.raw': 'Raw records',
      'work.room.site.thinking': 'Thinking',
      'work.room.site.recordJson': 'View JSON',
      'work.room.site.fullOutput': 'View complete output',
      'work.room.site.subagent': 'Subagent records',
      'work.room.site.failed': 'Failed',
      'work.room.site.showImage': 'Show image',
    })[key] ?? key,
}))

function record(id: string, parts: RecordPart[], author = 'cheese-a'): Block {
  return {
    id,
    conversation_id: 'room',
    turn_id: 'turn',
    author,
    author_type: 'participant',
    kind: 'event',
    content: '',
    created_at: `2026-10-19T10:00:${id.padStart(2, '0')}Z`,
    refs: [],
    meta: {
      record: { type: 'assistant', message: { content: parts } },
      parts,
      tool: parts.some((part) => part.kind === 'tool') ? 'Bash' : undefined,
    },
  } as Block
}
const tool = (id: string, command: string, author?: string) =>
  record(id, [{ kind: 'tool', name: 'Bash', input: { command }, preview: command }], author)
function open() {
  return render(
    defineComponent({
      setup() {
        const site = usePanelSite({
          topicId: () => 'room',
          active: () => true,
          refreshTick: () => 0,
          runningTurns: () => ({}),
          agentControl: () => null,
        })
        return () =>
          h(PanelSite, { topicId: 'room', active: true, memberNames: { 'cheese-a': 'A', 'cheese-b': 'B' }, site })
      },
    })
  )
}
beforeEach(() => {
  // 现场那一窗存在模块级的表里：前一条用例留下的窗口会变成这一条的「上次打开」。
  forgetWindows()
  storageRemove('cheese.site.view')
  api.getTranscript.mockReset()
  api.sessionRecordImageUrl.mockReset()
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})
afterEach(cleanup)

describe('the header stays put', () => {
  it('keeps the view switch on screen while records load and when there are none', async () => {
    let answer!: (value: { data: Block[]; has_more: boolean }) => void
    api.getTranscript.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve
        })
    )
    const view = open()

    // 记录还在路上：表头（会话栏 + 切换）照常画，骨架只在它下面。
    expect(view.container.querySelector('.site-head')).toBeTruthy()
    expect(view.container.querySelector('.skel--site')).toBeTruthy()
    expect(view.getByRole('button', { name: 'Raw records' })).toBeTruthy()

    answer({ data: [], has_more: false })
    await waitFor(() => expect(view.container.textContent).toContain('work.room.site.empty'))
    expect(view.container.querySelector('.site-head')).toBeTruthy()
    expect(view.getByRole('button', { name: 'Raw records' })).toBeTruthy()
  })
})

describe('a record with nothing to show', () => {
  it('gets no group head of its own in the overview', async () => {
    // 输入排队这类平台记账行没有可显示的内容。它们没有轮次 id，一个个都单独成组，
    // 组头就会顶出一串「时刻 · 0 步」、底下什么也没有。
    const queued = record('1', [])
    queued.turn_id = null
    queued.created_at = '2026-10-19T10:00:01Z'
    queued.meta = {
      record: { type: 'command_lifecycle', state: 'queued' },
      parts: [],
      platform: false,
    } as Block['meta']
    api.getTranscript.mockResolvedValue({ data: [queued, tool('2', 'make test')], has_more: false })
    const view = open()
    await waitFor(() => expect(view.getAllByText('make test').length).toBeGreaterThan(0))

    expect(view.container.querySelectorAll('.turn__head'), '记账行不该有自己的组头').toHaveLength(1)
    // 原始记录视图照旧把它们摊开。
    await fireEvent.click(view.getByRole('button', { name: 'Raw records' }))
    expect(view.getByText('command_lifecycle')).toBeTruthy()
  })

  it('keeps a turn that has not drawn a step yet, with its head', async () => {
    // 一轮开头那条 `command_lifecycle/started` 也没有可显示的内容，但它属于这一轮：
    // 丢掉它，这一轮连组头一起消失，直到第一个步骤到达。
    const started = record('1', [])
    started.turn_id = 'turn-1'
    started.created_at = '2026-10-19T10:00:01Z'
    started.meta = {
      record: { type: 'command_lifecycle', state: 'started' },
      parts: [],
      platform: false,
    } as Block['meta']
    api.getTranscript.mockResolvedValue({ data: [started], has_more: false })
    const view = open()
    await waitFor(() => expect(view.container.querySelectorAll('.turn__head')).toHaveLength(1))
  })
})

describe('raw records and overview share one timeline', () => {
  it('starts in overview, reveals thinking in raw view, and remembers the choice', async () => {
    api.getTranscript.mockResolvedValue({
      data: [record('1', [{ kind: 'thinking', text: 'Consider the missing input' }]), tool('2', 'make test')],
      has_more: false,
    })
    let view = open()
    await waitFor(() => expect(view.getAllByText('make test').length).toBeGreaterThan(0))
    expect(view.queryByText('Consider the missing input')).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: 'Raw records' }))
    expect(view.getByText('Thinking')).toBeTruthy()
    expect(view.getByText('Consider the missing input')).toBeTruthy()
    view.unmount()
    view = open()
    await waitFor(() => expect(view.getByText('Thinking')).toBeTruthy())
    expect(view.getByRole('button', { name: 'Raw records' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('makes complete tool arguments and the beginning and end of output available', async () => {
    const command = 'printf ' + 'x'.repeat(5000)
    const output = 'HEAD\n' + 'output\n'.repeat(3000) + 'END'
    api.getTranscript.mockResolvedValue({
      data: [tool('1', command), record('2', [{ kind: 'output', text: output, failed: false }])],
      has_more: false,
    })
    const view = open()
    await waitFor(() => expect(view.container.textContent).toContain(command))
    expect(view.container.textContent).toContain(output)
  })

  it('shows unknown raw records without exposing encrypted thinking', async () => {
    const row = record('1', [])
    row.meta!.record = {
      type: 'future-record',
      payload: 'new field',
      thinking: { type: 'thinking', signature: 'hidden-signature', encrypted_content: 'hidden-ciphertext' },
      input: { signature: 'ordinary argument' },
    }
    api.getTranscript.mockResolvedValue({ data: [row], has_more: false })
    const view = open()
    await fireEvent.click(await view.findByRole('button', { name: 'Raw records' }))
    expect(view.container.textContent).toContain('future-record')
    expect(view.container.textContent).toContain('new field')
    expect(view.container.textContent).not.toContain('hidden-signature')
    expect(view.container.textContent).not.toContain('hidden-ciphertext')
    expect(view.container.textContent).toContain('ordinary argument')
  })

  it('reads earlier records with the server cursor', async () => {
    api.getTranscript
      .mockResolvedValueOnce({ data: [tool('2', 'newest')], has_more: true, oldest_id: 'opaque-cursor' })
      .mockResolvedValue({ data: [tool('1', 'earlier')], has_more: false })
    const view = open()
    await view.findAllByText('newest')
    const pane = view.container.querySelector('.panel-site')!
    await fireEvent.scroll(pane)
    await waitFor(() =>
      expect(api.getTranscript).toHaveBeenCalledWith('room', { limit: 120, author: null, before: 'opaque-cursor' })
    )
    expect((await view.findAllByText('earlier')).length).toBeGreaterThan(0)
  })

  it('filters before paging when a teammate is selected', async () => {
    api.getTranscript
      .mockResolvedValueOnce({ data: [tool('1', 'one', 'cheese-a'), tool('2', 'two', 'cheese-b')], has_more: false })
      .mockResolvedValue({ data: [tool('3', 'only A', 'cheese-a')], has_more: false })
    const view = open()
    await fireEvent.click(await view.findByRole('tab', { name: 'A' }))
    // 换视角 = 重新开这一格：和打开一样，先问数据库里已有的那一版（`resident`），
    // 少了归档部分再整页补齐。
    await waitFor(() =>
      expect(api.getTranscript).toHaveBeenCalledWith('room', {
        limit: 120,
        author: 'cheese-a',
        resident: true,
      })
    )
    expect((await view.findAllByText('only A')).length).toBeGreaterThan(0)
    expect(view.queryByText('two')).toBeNull()
  })

  it('keeps subagent records under their parent call and retains their complete conclusion', async () => {
    const parent = record('1', [
      {
        kind: 'tool',
        name: 'Agent',
        call_id: 'parent-call',
        input: { prompt: 'Inspect the parser' },
        preview: 'Inspect the parser',
      },
    ])
    const conclusion = 'The parser accepts the record. ' + 'Detailed evidence. '.repeat(60)
    const child = record('2', [{ kind: 'text', text: conclusion }])
    child.meta!.parent = 'parent-call'
    api.getTranscript.mockResolvedValue({ data: [parent, child], has_more: false })
    const view = open()
    expect(await view.findByText('Subagent records')).toBeTruthy()
    expect(view.container.textContent).toContain(conclusion)
    await fireEvent.click(view.getByRole('button', { name: 'Raw records' }))
    expect(view.container.textContent).toContain(conclusion)
  })

  it('marks a failed result on the call it belongs to', async () => {
    const call = record('1', [
      { kind: 'tool', name: 'Bash', call_id: 'call', input: { command: 'make test' }, preview: 'make test' },
    ])
    const result = record('2', [
      { kind: 'output', call_id: 'call', text: 'Compiler error with complete diagnostics', failed: true },
    ])
    api.getTranscript.mockResolvedValue({ data: [call, result], has_more: false })
    const view = open()
    await waitFor(() => expect(view.getAllByText('Failed')).toHaveLength(2))
    expect(view.getByText('Compiler error with complete diagnostics')).toBeTruthy()
  })

  it('loads image bytes only after the member requests them', async () => {
    const image = record('1', [{ kind: 'image', source: { type: 'session_image', sha256: 'a'.repeat(64) } }])
    api.getTranscript.mockResolvedValue({ data: [image], has_more: false })
    api.sessionRecordImageUrl.mockResolvedValue('blob:sample-image')
    const view = open()
    const button = await view.findByRole('button', { name: 'Show image' })
    expect(api.sessionRecordImageUrl).not.toHaveBeenCalled()
    await fireEvent.click(button)
    await waitFor(() => expect(api.sessionRecordImageUrl).toHaveBeenCalledWith('room', 'a'.repeat(64)))
    await waitFor(() => expect(view.container.querySelector('img')?.src).toBe('blob:sample-image'))
  })

  it('groups platform status transitions in overview and exposes each fact in raw view', async () => {
    const first = { ...record('1', []), run_record: true, content: 'Waiting', meta: { event_type: 'device_waiting' } }
    const last = {
      ...record('2', []),
      run_record: true,
      content: 'Reconnected',
      meta: { event_type: 'device_waiting', series_id: first.id },
    }
    api.getTranscript.mockResolvedValue({ data: [first, last], has_more: false })
    const view = open()
    await waitFor(() => expect(view.container.textContent).toContain('Reconnected'))
    expect(view.container.textContent).not.toContain('Waiting')
    await fireEvent.click(view.getByRole('button', { name: 'Raw records' }))
    expect(view.container.textContent).toContain('Waiting')
    expect(view.container.textContent).toContain('Reconnected')
  })

  it('names the member a platform line is about, as the room names them', async () => {
    const edited = {
      ...record('1', []),
      run_record: true,
      turn_id: null,
      content: '<@cheese-b> 改了一条之前的消息',
      meta: { event_type: 'message_edited' },
    }
    api.getTranscript.mockResolvedValue({ data: [edited], has_more: false })
    const view = open()
    await waitFor(() => expect(view.container.textContent).toContain('B 改了一条之前的消息'))
    expect(view.container.textContent).not.toContain('cheese-b')
  })

  it('keeps prose, status lines, and 3-line previews selectable without toggling the fold', async () => {
    vi.stubEnv('DEV', false)
    ;(window as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: async () => undefined } }
    const { behaveAsDesktopApp } = await import('../../lib/desktopNative')
    behaveAsDesktopApp()
    try {
      const longOutput = 'OUT-LINE-1\nOUT-LINE-2\nOUT-LINE-3\n' + 'more output\n'.repeat(20)
      const longProse = 'PROSE-LINE-1\nPROSE-LINE-2\nPROSE-LINE-3\n' + 'more prose\n'.repeat(20)
      const platformNotice = {
        ...record('1', []),
        run_record: true,
        turn_id: null,
        content: '平台状态提示可复制',
        meta: { event_type: 'message_edited' },
      }
      api.getTranscript.mockResolvedValue({
        data: [
          platformNotice,
          record('2', [
            { kind: 'text', text: '短正文可复制' },
            { kind: 'status', label: 'retry', text: '重试状态可复制' },
          ]),
          record('3', [{ kind: 'output', text: longOutput, failed: false }]),
          record('4', [{ kind: 'text', text: longProse }]),
        ],
        has_more: false,
      })
      const view = open()
      const statusEl = await view.findByText('平台状态提示可复制')
      const partStatusEl = await view.findByText(/重试状态可复制/)
      const proseEl = await view.findByText('短正文可复制')
      const outPreview = await view.findByText('OUT-LINE-1 OUT-LINE-2 OUT-LINE-3')
      const prosePreview = await view.findByText('PROSE-LINE-1 PROSE-LINE-2 PROSE-LINE-3')

      for (const el of [statusEl, partStatusEl, proseEl, outPreview, prosePreview]) {
        const menu = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
        el.dispatchEvent(menu)
        expect(menu.defaultPrevented).toBe(false)
      }
      for (const preview of [outPreview, prosePreview]) {
        expect(preview.closest('summary')).toBeNull()
      }
    } finally {
      vi.unstubAllEnvs()
      delete (window as { __TAURI__?: unknown }).__TAURI__
      delete document.documentElement.dataset.desktopApp
      document.head.querySelectorAll('style').forEach((s) => s.remove())
    }
  })
})
