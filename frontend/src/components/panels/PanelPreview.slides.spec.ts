import type { Topic } from '@/cx_types'
import type { PreviewQuestion, SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import TopicChatColumn from '../../views/workspace/TopicChatColumn.vue'

import PanelPreviewView from './PanelPreviewView.vue'

import i18n, { setLocale } from '@/i18n'

const text = 'Whole page original PDF text '.repeat(30)
const pdfText = vi.hoisted(() => ({ text: 'Whole page original PDF text '.repeat(30) }))
const context = { topicId: 'room', path: 'deck.pptx', source: 'committed' as const, taskId: 'task', version: 'v7' }
vi.mock('./preview/PreviewSlides.vue', () => ({
  default: {
    props: ['data', 'context'],
    emits: ['quote', 'pageContext'],
    setup(props: { context: unknown }, { emit }: { emit: (event: string, payload: unknown) => void }) {
      return () =>
        h('div', { 'data-testid': 'slides' }, [
          h('button', { onClick: () => emit('quote', { text: 'q'.repeat(250), page: 2 }) }, 'quote'),
          h(
            'button',
            {
              onClick: () =>
                emit('pageContext', { text: pdfText.text, page: 2, scope: 'page', context: props.context }),
            },
            'page'
          ),
          h(
            'button',
            {
              onClick: () =>
                emit('pageContext', {
                  text: 'Selected run on the slide',
                  page: 2,
                  scope: 'selection',
                  context: props.context,
                  prefix: '前面那句',
                  suffix: '后面那句',
                }),
            },
            'selection'
          ),
        ])
    },
  },
}))
// 真组件把 clearMark 交出来（抹掉指过的那一点）；替身也得有，不然面板清位置时炸。
vi.mock('./preview/PreviewPages.vue', () => ({
  default: {
    template: '<div data-testid="pages" />',
    setup(_props: unknown, { expose }: { expose: (api: { clearMark: () => void }) => void }) {
      expose({ clearMark: () => {} })
    },
  },
}))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))
const docBytes = new ArrayBuffer(8)
const props = {
  topicId: 'room',
  projectId: 'project',
  path: 'deck.pptx',
  frameName: 'frame',
  loading: false,
  refreshing: false,
  previewFile: {
    path: 'deck.pptx',
    content: null,
    version: 'v7',
    bytes: 8,
    binary: true,
    too_large: false,
    source: context.source,
  },
  previewMime: '',
  previewNamed: true,
  previewUrl: null,
  previewAppNote: '',
  previewTunnelUp: false,
  previewNamedPath: 'deck.pptx',
  previewError: null,
  previewReadError: null,
  documentSuffix: 'pptx',
  documentType: { view: 'pages' as const, label: 'slides', icon: 'mdi-file-powerpoint-outline' },
  documentName: 'deck.pptx',
  isImageArtifact: false,
  downloadError: '',
  docBytes,
  docIdentity: context,
  docSnapshot: { bytes: docBytes, identity: context, sourceVersion: context.version },
  docLoading: false,
  docError: '',
  docRendererMissing: false,
  slideContext: context,
}
beforeEach(() => {
  setLocale('zh-CN')
  pdfText.text = text
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
function mount(submitQuestion?: SubmitPreviewQuestion) {
  return render(PanelPreviewView, {
    props: { ...props, submitQuestion },
    global: {
      stubs: {
        VBtn: { template: '<button><slot /></button>' },
        VIcon: true,
        VSpacer: true,
        VAlert: true,
        VDialog: true,
      },
    },
  })
}
it('mounts the slides branch and keeps quote normalization capped at 200 characters', async () => {
  const ui = mount()
  expect(ui.queryByTestId('pages')).toBeNull()
  await fireEvent.click(ui.getByText('quote'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'fix it')
  await fireEvent.click(ui.getByText('发送'))
  const payload: unknown = ui.emitted().locate?.[0]
  const first = Array.isArray(payload) ? (payload[0] as { message?: unknown } | undefined) : undefined
  if (typeof first?.message !== 'string') throw new Error('Expected locate to emit a message')
  const message = first.message
  expect(message).toContain('q'.repeat(200))
  expect(message).not.toContain('q'.repeat(201))
  await fireEvent.click(ui.getByRole('button', { name: /^下载$/ }))
  expect(ui.emitted().download).toHaveLength(1)
})
it('sends whole-page PDF context with complete text and the verified file identity', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit)
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'explain this page')
  await fireEvent.click(ui.getByText('发送'))
  const request = submit.mock.calls[0]?.[0]
  expect(request).toMatchObject({ topicId: 'room', intent: 'ask-agent' })
  expect(request.content).toBe('explain this page')
  expect(request.quotedContext).toEqual({
    kind: 'slide-page',
    path: 'deck.pptx',
    source: 'committed',
    task_id: 'task',
    version: 'v7',
    page: 2,
    scope: 'page',
    text,
  })
})
it('sends a selected run on the slide as a quoted context, marked as a selection', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit)
  await fireEvent.click(ui.getByText('selection'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'this line is wrong')
  await fireEvent.click(ui.getByText('发送'))
  const request = submit.mock.calls[0]?.[0]
  expect(request.content).toBe('this line is wrong')
  expect(request.quotedContext).toEqual({
    kind: 'slide-page',
    path: 'deck.pptx',
    source: 'committed',
    task_id: 'task',
    version: 'v7',
    page: 2,
    scope: 'selection',
    text: 'Selected run on the slide',
    // 两侧的字跟着出去：受话人靠它分辨同一句话在这一页的哪一处出现。
    prefix: '前面那句',
    suffix: '后面那句',
  })
})
it('drops a selected run whose version moved on before send, like the whole-page path', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit)
  await fireEvent.click(ui.getByText('selection'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'this line is wrong')
  await ui.rerender({ ...props, previewFile: { ...props.previewFile, version: 'v8' } })
  await fireEvent.click(ui.getByText('发送'))
  expect(submit).not.toHaveBeenCalled()
  // 被挡下的是「这一下」，不是这段说明：引用和说明都原样留在面板里，读者把版本对
  // 回来就能再发一次。换成 `emitted().locate` 是测不出东西的——这一支只发
  // `pageContext`，回退那句拼话根本不在这条路上。
  expect(ui.getByText('Selected run on the slide')).toBeTruthy()
  expect((ui.getByPlaceholderText('说明要改什么') as HTMLInputElement).value).toBe('this line is wrong')
})
it('retires the locator when bytes or source identity change and routes PDF to the existing reader', async () => {
  const ui = mount()
  await fireEvent.click(ui.getByText('page'))
  const nextProps = {
    ...props,
    slideContext: { ...context, version: 'v8' },
    docBytes: new ArrayBuffer(16),
  }
  await ui.rerender(nextProps)
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
  await ui.rerender({ ...nextProps, documentSuffix: 'pdf' })
  expect(ui.getByTestId('pages')).toBeTruthy()
  expect(ui.queryByTestId('slides')).toBeNull()
})

it('does not open whole-page context when displayed source bytes disagree with metadata', async () => {
  const ui = mount()
  await ui.rerender({ ...props, docSnapshot: { ...props.docSnapshot, sourceVersion: 'v8' } })
  await fireEvent.click(ui.getByText('page'))
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(ui.emitted().locate).toBeUndefined()
  await fireEvent.click(ui.getByText('quote'))
  expect(ui.getByPlaceholderText('说明要改什么')).toBeTruthy()
})

it('rechecks current metadata at send time without relying on locator retirement', async () => {
  const ui = mount()
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'explain this page')
  await ui.rerender({ ...props, previewFile: { ...props.previewFile, version: 'v8' } })
  await fireEvent.click(ui.getByText('发送'))
  expect(ui.emitted().locate).toBeUndefined()
})

// Keep the column, roster, composer preparation, outbox and HTTP serialization
// real. Only the PDF text reader and remote server are substitutes here.
function room(id: string): Topic {
  return { id, project_id: 'project', title: id, kind: 'topic', status: 'active' } as Topic
}
function chatPreview(id: string, rosterGate = Promise.resolve(), refused = false) {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'reader' }))
  const posted: {
    url: string
    body: { content: string; request_id: string; quoted_context?: Record<string, unknown> }
  }[] = []
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const path = String(url)
    if (init?.method === 'POST' && path.endsWith('/messages')) {
      const body = JSON.parse(String(init.body))
      posted.push({ url: path, body })
      if (refused) return { ok: false, status: 422, json: async () => ({ code: 422, message: '这个请求需要修改' }) }
      // An unknown receipt stays in the existing outbox. It is not replayed
      // through a second question API or given a new request identity.
      throw new TypeError('Failed to fetch')
    }
    if (path.includes('/members')) await rosterGate
    return {
      ok: true,
      status: 200,
      json: async () => ({
        code: 200,
        data: path.includes('/members')
          ? {
              data: [{ id: 'seat', member_handle: 'cheese-current', name: '评审', role: 'member', agent: true }],
              total: 1,
            }
          : path.includes('/progress')
            ? { items: [], updated_at: null }
            : { data: [], total: 0, has_more: false },
      }),
    }
  })
  const chatTopic = ref(room(id))
  const identity = { ...context, topicId: id }
  const chat = ref<{
    say: (message: string) => boolean
    submitQuestion?: SubmitPreviewQuestion
  } | null>(null)
  const Wrapper = defineComponent({
    setup: () => () =>
      h('div', [
        h(TopicChatColumn, { ref: chat, topic: chatTopic.value, members: [], topicList: [] }),
        h(PanelPreviewView, {
          ...props,
          topicId: id,
          docIdentity: identity,
          docSnapshot: { ...props.docSnapshot, identity },
          slideContext: identity,
          submitQuestion: (request: PreviewQuestion) => chat.value?.submitQuestion?.(request) ?? false,
          onLocate: (payload: { message: string }) => chat.value?.say(payload.message),
        }),
      ]),
  })
  const ui = render(Wrapper, {
    global: {
      plugins: [createVuetify({ components, directives }), createPinia(), i18n],
      stubs: { TopicAcceptCard: true, AgentFeedbackCard: true },
    },
  })
  return { ...ui, posted, chatTopic }
}
async function pageQuestion(ui: ReturnType<typeof chatPreview>, note = 'explain this page') {
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), note)
  await fireEvent.click(ui.getByPlaceholderText('说明要改什么').closest('.locator')!.querySelector('button')!)
}
it('whole-page ask posts the current canonical AI mention through the normal message outbox', async () => {
  const ui = chatPreview('room-post')
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await pageQuestion(ui)
  await waitFor(() => expect(ui.posted).toHaveLength(1))
  expect(ui.posted[0].url).toContain('/topics/room-post/messages')
  expect(ui.posted[0].body.content).toMatch(/^<@cheese-current> /)
  expect(ui.posted[0].body.content).toBe('<@cheese-current> explain this page')
  expect(ui.posted[0].body.quoted_context).toEqual({
    kind: 'slide-page',
    path: 'deck.pptx',
    source: 'committed',
    task_id: 'task',
    version: 'v7',
    page: 2,
    scope: 'page',
    text,
  })
  expect(ui.posted[0].body.request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  await waitFor(() => expect(ui.container.textContent).toContain('等待连接'))
  ui.chatTopic.value = room('room-away')
  await waitFor(() => expect(ui.container.textContent).not.toContain('explain this page'))
  ui.chatTopic.value = room('room-post')
  await waitFor(() => expect(ui.posted).toHaveLength(2))
  expect(ui.posted[1]).toEqual(ui.posted[0])
})
it.each(['  @评审\n', '<@cheese-other>', '@评审 <@cheese-current> <@cheese-other>'])(
  'page quote %s stays verbatim data through pending display and an unknown-receipt retry',
  async (quote) => {
    pdfText.text = quote
    const roomId = `room-quote-${quote.length}`
    const ui = chatPreview(roomId)
    await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
    await pageQuestion(ui)
    await waitFor(() => expect(ui.posted).toHaveLength(1))
    expect(ui.posted[0].body.content).toBe('<@cheese-current> explain this page')
    expect(ui.posted[0].body.quoted_context?.text).toBe(quote)
    const pendingQuote = ui.container.querySelector('.message-quote__text')
    expect(pendingQuote?.textContent).toBe(quote)
    expect(pendingQuote?.querySelector('[data-handle]')).toBeNull()
    pdfText.text = 'new page text after send'
    ui.chatTopic.value = room('room-away')
    await waitFor(() => expect(ui.container.textContent).not.toContain('explain this page'))
    ui.chatTopic.value = room(roomId)
    await waitFor(() => expect(ui.posted).toHaveLength(2))
    expect(ui.posted[1]).toEqual(ui.posted[0])
  }
)
it('keeps the whole-page question until this room roster arrives, then submits it once', async () => {
  let release!: () => void
  const ui = chatPreview(
    'room-wait',
    new Promise<void>((resolve) => {
      release = resolve
    })
  )
  try {
    await pageQuestion(ui)
    expect(ui.posted).toHaveLength(0)
    expect((ui.getByPlaceholderText('说明要改什么') as HTMLInputElement).value).toBe('explain this page')
  } finally {
    release()
  }
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await fireEvent.click(ui.getByPlaceholderText('说明要改什么').closest('.locator')!.querySelector('button')!)
  await waitFor(() => expect(ui.posted).toHaveLength(1))
  expect(ui.posted[0].body.content).toMatch(/^<@cheese-current> /)
})
it('editing a refused question keeps its frozen quote outside the composer text and resend body', async () => {
  pdfText.text = '  @评审 <@cheese-other> 原页\n'
  const ui = chatPreview('room-edit-quote', Promise.resolve(), true)
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await pageQuestion(ui)
  await waitFor(() => expect(ui.container.querySelector('.outbox-fail')).toBeTruthy())
  const first = ui.posted[0].body
  await fireEvent.click(ui.container.querySelectorAll('.outbox-fail button')[1])
  const draft = ui.container.querySelector('textarea')!
  expect(draft.value).toBe('<@cheese-current> explain this page')
  expect(ui.container.querySelector('.composer-quote .message-quote__text')?.textContent).toBe(
    '  @评审 <@cheese-other> 原页\n'
  )
  ui.chatTopic.value = room('room-edit-away')
  await waitFor(() => expect(ui.container.querySelector('.composer-quote')).toBeNull())
  ui.chatTopic.value = room('room-edit-quote')
  await waitFor(() => expect(ui.container.querySelector('.composer-quote')).toBeTruthy())
  const restored = ui.container.querySelector('textarea')!
  await fireEvent.update(restored, '<@cheese-current> explain again')
  await fireEvent.keyDown(restored, { key: 'Enter' })
  await waitFor(() => expect(ui.posted).toHaveLength(2))
  expect(ui.posted[1].body.content).toBe('<@cheese-current> explain again')
  expect(ui.posted[1].body.quoted_context).toEqual(first.quoted_context)
  expect(ui.posted[1].body.request_id).not.toBe(first.request_id)
})
it('does not send a page question into a different topic even when its AI roster is ready', async () => {
  const ui = chatPreview('room-mismatch')
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  ui.chatTopic.value = room('other-room')
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await pageQuestion(ui)
  expect(ui.posted).toHaveLength(0)
  expect((ui.getByPlaceholderText('说明要改什么') as HTMLInputElement).value).toBe('explain this page')
})

it('removing a refused-question reference sends only the edited authored message', async () => {
  pdfText.text = '原页 @评审'
  const ui = chatPreview('room-remove-quote', Promise.resolve(), true)
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await pageQuestion(ui)
  await waitFor(() => expect(ui.container.querySelector('.outbox-fail')).toBeTruthy())
  await fireEvent.click(ui.container.querySelectorAll('.outbox-fail button')[1])
  await fireEvent.click(ui.getByText('移除引用'))
  expect(ui.container.querySelector('.composer-quote')).toBeNull()
  await fireEvent.keyDown(ui.container.querySelector('textarea')!, { key: 'Enter' })
  await waitFor(() => expect(ui.posted).toHaveLength(2))
  expect(ui.posted[1].body.content).toBe('<@cheese-current> explain this page')
  expect(ui.posted[1].body.quoted_context).toBeUndefined()
})
it('editing a different refused quote leaves the existing quote and failed message available', async () => {
  pdfText.text = '原页 A'
  const ui = chatPreview('room-conflicting-quotes', Promise.resolve(), true)
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await pageQuestion(ui, 'question A')
  await waitFor(() => expect(ui.container.querySelectorAll('.outbox-fail')).toHaveLength(1))
  pdfText.text = '原页 B'
  await pageQuestion(ui, 'question B')
  await waitFor(() => expect(ui.container.querySelectorAll('.outbox-fail')).toHaveLength(2))
  await fireEvent.click(ui.container.querySelectorAll('.outbox-fail')[0].querySelectorAll('button')[1])
  await fireEvent.click(ui.container.querySelector('.outbox-fail')!.querySelectorAll('button')[1])
  expect(ui.container.querySelector('.composer-quote .message-quote__text')?.textContent).toBe('原页 A')
  expect(ui.container.querySelector('textarea')?.value).toBe('<@cheese-current> question A')
  expect(ui.container.querySelector('.outbox-fail')).toBeTruthy()
  expect(ui.container.textContent).toContain('输入框中已有另一份引用，请先发送或移除它。')
})
