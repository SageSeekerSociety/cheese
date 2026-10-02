import type { Topic } from '@/cx_types'

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
const context = { topicId: 'room', path: 'deck.pptx', source: 'committed' as const, taskId: 'task', version: 'v7' }
vi.mock('./preview/PreviewSlides.vue', () => ({
  default: {
    props: ['data', 'context'],
    emits: ['quote', 'pageContext'],
    template: `<div data-testid="slides"><button @click="$emit('quote', {text: '${'q'.repeat(250)}', page: 2})">quote</button><button @click="$emit('pageContext', {text: '${'Whole page original PDF text '.repeat(30)}', page: 2, scope: 'page', context})">page</button></div>`,
  },
}))
vi.mock('./preview/PreviewPages.vue', () => ({ default: { template: '<div data-testid="pages" />' } }))
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
beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
function mount(submitQuestion?: (request: { topicId: string; content: string; intent: 'ask-agent' }) => boolean) {
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
  if (!Array.isArray(payload) || typeof payload[0] !== 'string')
    throw new Error('Expected locate to emit a string message')
  const message = payload[0]
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
  const message = request.content
  expect(message).toContain(text)
  expect(message).toContain('整页 PDF 文字上下文')
  expect(message).toContain('topic=room source=committed task=task version=v7')
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
function chatPreview(id: string, rosterGate = Promise.resolve()) {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'reader' }))
  const posted: { url: string; body: { content: string; request_id: string } }[] = []
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
    submitQuestion?: (request: { topicId: string; content: string; intent: 'ask-agent' }) => boolean
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
          submitQuestion: (request: { topicId: string; content: string; intent: 'ask-agent' }) =>
            chat.value?.submitQuestion?.(request) ?? false,
          onLocate: (message: string) => chat.value?.say(message),
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
async function pageQuestion(ui: ReturnType<typeof chatPreview>) {
  await fireEvent.click(ui.getByText('page'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), 'explain this page')
  await fireEvent.click(ui.getByPlaceholderText('说明要改什么').closest('.locator')!.querySelector('button')!)
}
it('whole-page ask posts the current canonical AI mention through the normal message outbox', async () => {
  const ui = chatPreview('room-post')
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await pageQuestion(ui)
  await waitFor(() => expect(ui.posted).toHaveLength(1))
  expect(ui.posted[0].url).toContain('/topics/room-post/messages')
  expect(ui.posted[0].body.content).toMatch(/^<@cheese-current> /)
  expect(ui.posted[0].body.content).toContain(text.trimEnd())
  expect(ui.posted[0].body.content).toContain('topic=room-post source=committed task=task version=v7')
  expect(ui.posted[0].body.request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  await waitFor(() => expect(ui.container.textContent).toContain('等待连接'))
  ui.chatTopic.value = room('room-away')
  await waitFor(() => expect(ui.container.textContent).not.toContain('explain this page'))
  ui.chatTopic.value = room('room-post')
  await waitFor(() => expect(ui.posted).toHaveLength(2))
  expect(ui.posted[1]).toEqual(ui.posted[0])
})
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
it('does not send a page question into a different topic even when its AI roster is ready', async () => {
  const ui = chatPreview('room-mismatch')
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  ui.chatTopic.value = room('other-room')
  await waitFor(() => expect(ui.container.querySelector('.summon-btn')?.hasAttribute('disabled')).toBe(false))
  await pageQuestion(ui)
  expect(ui.posted).toHaveLength(0)
  expect((ui.getByPlaceholderText('说明要改什么') as HTMLInputElement).value).toBe('explain this page')
})
