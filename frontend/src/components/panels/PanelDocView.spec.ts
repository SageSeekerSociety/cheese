// @vitest-environment jsdom
import type { DocComment, DocThreadActions, DocThreadState } from '../../lib/docThreadTypes'

import { reactive } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DOC_TOPIC, docPanelProps } from '../../views/demo/catalogFixtures'

import PanelDocView from './PanelDocView.vue'

import { setLocale } from '@/i18n'

let serial = 0
beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this.classList.contains('doc-pane') ? 1000 : 300
    return { x: 0, y: 0, left: 0, top: 0, width, right: width, height: 600, bottom: 600, toJSON: () => ({}) }
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

async function openThread() {
  const topic = { ...DOC_TOPIC, id: `thread-context-${++serial}` }
  const comment: DocComment = {
    id: 'comment-a',
    content: '当前段落的评论',
    author: 'reader',
    anchor_quote: null,
    created_at: '2026-10-02T00:00:00Z',
  }
  const state = reactive<DocThreadState>({
    threads: [{ comment, revision: 1, state: 'open', replies: [] }],
    activity: {},
    errors: {},
    busy: false,
    unknown: null,
  })
  const actions: DocThreadActions = {
    reply: vi.fn(async () => {}),
    resolve: vi.fn(async () => {}),
    reopen: vi.fn(async () => {}),
    recover: vi.fn(async () => undefined),
    stopAgent: vi.fn(async () => {}),
  }
  const { rerender } = render(PanelDocView, {
    props: {
      ...docPanelProps(),
      topic,
      commentAuthor: 'reader',
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
    expect(localStorage.getItem(`cheese.doc-thread.draft.v1:reader:${f.topic.id}:${f.comment.id}`)).toBe(
      '保留正在输入的回复'
    )
  })
})
