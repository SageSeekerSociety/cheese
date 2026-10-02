// @vitest-environment jsdom
import type { Block } from '../../cx_types'
import type { DocThreadActions, DocThreadState } from '../../lib/docThreadTypes'

import { defineComponent, h, reactive } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DOC_TOPIC, docPanelProps } from '../../views/demo/catalogFixtures'

import DocAiPanel from './doc/DocAiPanel.vue'
import PanelDocView from './PanelDocView.vue'

import { setLocale } from '@/i18n'

let serial = 0
beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this.classList.contains('doc-reading') ? 1000 : 300
    return { x: 0, y: 0, left: 0, top: 0, width, right: width, height: 600, bottom: 600, toJSON: () => ({}) }
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

async function openThread() {
  const topic = { ...DOC_TOPIC, id: `thread-context-${++serial}` }
  const comment = {
    id: 'comment-a',
    topic_id: topic.id,
    kind: 'comment',
    content: '当前段落的评论',
    author: 'reader',
    created_at: '2026-10-02T00:00:00Z',
  } as Block
  const state = reactive<DocThreadState>({
    threads: { [comment.id]: { comment, revision: 1, state: 'open', anchor: null, replies: [] } },
    errors: {},
    busy: false,
    unknown: null,
  })
  const actions: DocThreadActions = {
    load: vi.fn(async () => {}),
    reply: vi.fn(async () => {}),
    resolve: vi.fn(async () => {}),
    reopen: vi.fn(async () => {}),
    recover: vi.fn(async () => undefined),
  }
  const { rerender } = render(PanelDocView, {
    props: {
      ...docPanelProps(),
      topic,
      commentAuthor: 'reader',
      comments: [comment],
      threadState: state,
      threadActions: actions,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await fireEvent.click(screen.getByRole('button', { name: /^评论$/ }))
  await fireEvent.click(await screen.findByText(comment.content))
  const input = (await screen.findByRole('textbox', { name: /^回复$/ })) as HTMLTextAreaElement
  await fireEvent.update(input, '保留正在输入的回复')
  return { topic, comment, input, rerender }
}

describe('document comment thread context', () => {
  it('keeps the open thread and reply draft when the same topic metadata is refreshed', async () => {
    const f = await openThread()
    await f.rerender({ topic: { ...f.topic, title: '同一话题的新标题' } })
    expect(screen.getByRole('textbox', { name: /^回复$/ })).toBe(f.input)
    expect(f.input.value).toBe('保留正在输入的回复')
  })

  it.each(['topic', 'actor'] as const)('closes the old thread when its %s identity changes', async (identity) => {
    const f = await openThread()
    if (identity === 'topic') await f.rerender({ topic: { ...f.topic, id: `${f.topic.id}-other` } })
    else await f.rerender({ commentAuthor: 'another-reader' })
    expect(screen.queryByRole('textbox', { name: /^回复$/ })).toBeNull()
    expect(localStorage.getItem(`cheese.doc-thread.draft.v1:reader:${f.comment.topic_id}:${f.comment.id}`)).toBe(
      '保留正在输入的回复'
    )
  })
})

describe('document tools workspace', () => {
  it('switches AI and comments in one pane without losing question, unknown recovery or reply draft', async () => {
    const topic = { ...DOC_TOPIC, id: `tools-context-${++serial}` }
    const comment = {
      id: 'tools-comment',
      topic_id: topic.id,
      kind: 'comment',
      content: '需要继续回复的评论',
      author: 'reader',
      created_at: '2026-10-02T00:00:00Z',
    } as Block
    const threadState = reactive<DocThreadState>({
      threads: { [comment.id]: { comment, revision: 1, state: 'open', anchor: null, replies: [] } },
      errors: {},
      busy: false,
      unknown: null,
    })
    const threadActions: DocThreadActions = {
      load: vi.fn(async () => {}),
      reply: vi.fn(async () => {}),
      resolve: vi.fn(async () => {}),
      reopen: vi.fn(async () => {}),
      recover: vi.fn(async () => undefined),
    }
    const ai = reactive({ opened: false, question: '', busy: false, unknown: true })
    const prepare = vi.fn(() => {
      ai.opened = true
    })
    const recover = vi.fn()
    render(
      defineComponent({
        setup: () => () =>
          h(
            PanelDocView,
            {
              ...docPanelProps(),
              topic,
              commentAuthor: 'reader',
              comments: [comment],
              threadState,
              threadActions,
              aiOpened: ai.opened,
              onOpenAi: prepare,
              onCloseAi: () => (ai.opened = false),
            },
            {
              ai: () =>
                h(DocAiPanel, {
                  cards: [],
                  question: ai.question,
                  busy: ai.busy,
                  error: '',
                  selectionStatus: '',
                  hasSelection: true,
                  blocked: false,
                  unknown: ai.unknown,
                  version: 1,
                  'onUpdate:question': (value: string) => (ai.question = value),
                  onRecover: recover,
                }),
            }
          ),
      }),
      { global: { plugins: [createVuetify({ components, directives })] } }
    )
    const launcher = screen.getByRole('button', { name: /^文档 AI$/ })
    launcher.focus()
    await fireEvent.click(launcher)
    const question = screen.getByRole('textbox', { name: '问题或修改要求' }) as HTMLTextAreaElement
    await fireEvent.update(question, '保留这个问题')
    await fireEvent.keyDown(screen.getByRole('tab', { name: /^文档 AI$/ }), { key: 'ArrowRight' })
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: /^评论/ }))
    expect(screen.queryByRole('textbox', { name: '问题或修改要求' })).toBeNull()
    await fireEvent.click(screen.getByText(comment.content))
    const reply = screen.getByRole('textbox', { name: /^回复$/ }) as HTMLTextAreaElement
    await fireEvent.update(reply, '未发出的线程回复')
    await fireEvent.click(screen.getByRole('tab', { name: /^文档 AI$/ }))
    expect(screen.getByRole('textbox', { name: '问题或修改要求' })).toBe(question)
    expect(question.value).toBe('保留这个问题')
    expect(prepare).toHaveBeenCalledTimes(1)
    await fireEvent.click(screen.getByRole('button', { name: '恢复原操作' }))
    expect(recover).toHaveBeenCalledTimes(1)
    ai.busy = true
    await fireEvent.click(screen.getByRole('tab', { name: /^评论/ }))
    expect(screen.getByRole('textbox', { name: /^回复$/ })).toBe(reply)
    expect(reply.value).toBe('未发出的线程回复')
    await fireEvent.click(screen.getByRole('tab', { name: /^文档 AI$/ }))
    expect(screen.getByRole('region', { name: '文档 AI' }).getAttribute('aria-busy')).toBe('true')
    expect(question.value).toBe('保留这个问题')
    await fireEvent.click(screen.getByText('返回正文'))
    expect(document.activeElement).toBe(launcher)
    expect(screen.queryByRole('tab', { name: /^文档 AI$/ })).toBeNull()
    expect(ai.busy).toBe(true)
    expect(ai.unknown).toBe(true)
  })
})
