import type { Block } from '../../../cx_types'
import type { DocThreadActions, DocThreadState } from '../../../lib/docThreadTypes'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DocComments from './DocComments.vue'

import { setLocale } from '@/i18n'

let serial = 0
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(sendComment = vi.fn(async () => undefined)) {
  const topicId = `draft-test-${++serial}`
  const view = render(DocComments, {
    props: { topicId, author: 'reader', comments: [], anchorNodes: [], sendComment },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { ...view, topicId, sendComment }
}
async function open() {
  await fireEvent.click(screen.getByTitle('写评论'))
  return screen.getByRole('textbox') as HTMLTextAreaElement
}
function pending() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe('document comment drafts', () => {
  it.each([{ isComposing: true }, { keyCode: 229 }, { shiftKey: true }])(
    'does not post an IME or newline Enter: %o',
    async (options) => {
      const view = mount()
      const input = await open()
      await fireEvent.update(input, '正在输入中文')
      await fireEvent.keyDown(input, { key: 'Enter', ...options })
      expect(view.sendComment).not.toHaveBeenCalled()
      expect(input.value).toBe('正在输入中文')
    }
  )

  it('posts plain Enter and clears only the successfully submitted draft', async () => {
    const view = mount()
    const input = await open()
    await fireEvent.update(input, '评论原稿')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(view.sendComment).toHaveBeenCalledWith(view.topicId, '评论原稿', undefined, ''))
    await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())
    expect(view.emitted().posted).toHaveLength(1)
  })

  it('retains text after a failed send and allows retry', async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(undefined)
    const view = mount(send)
    const input = await open()
    await fireEvent.update(input, '不能丢掉')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await screen.findByText('offline')
    expect(input.value).toBe('不能丢掉')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())
    expect(view.sendComment).toHaveBeenCalledTimes(2)
  })

  it('does not clear new edits when an older send succeeds', async () => {
    const receipt = pending()
    const view = mount(vi.fn(() => receipt.promise))
    const input = await open()
    await fireEvent.update(input, '已送出')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await fireEvent.update(input, '等待期间的新草稿')
    receipt.resolve()
    await waitFor(() => expect(view.emitted().posted).toHaveLength(1))
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('等待期间的新草稿')
  })

  it('keeps topic drafts separate and ignores the old topic receipt', async () => {
    const receipt = pending()
    const view = mount(vi.fn(() => receipt.promise))
    const input = await open()
    await fireEvent.update(input, '房间一已发送')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await view.rerender({ topicId: `${view.topicId}-other` })
    const next = await open()
    await fireEvent.update(next, '房间二草稿')
    receipt.resolve()
    await waitFor(() => expect(view.sendComment).toHaveBeenCalledTimes(1))
    await Promise.resolve()
    expect(next.value).toBe('房间二草稿')
    expect(view.emitted().posted).toBeUndefined()
    await view.rerender({ topicId: view.topicId })
    expect(screen.queryByRole('textbox')).toBeNull()
    await view.rerender({ topicId: `${view.topicId}-other` })
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('房间二草稿')
  })

  it('restores an unsent draft after switching topics and remounting', async () => {
    const view = mount()
    const input = await open()
    await fireEvent.update(input, '离开再回来')
    await view.rerender({ topicId: `${view.topicId}-other` })
    await open()
    await view.rerender({ topicId: view.topicId })
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('离开再回来')
    view.unmount()
    render(DocComments, {
      props: { topicId: view.topicId, author: 'reader', comments: [], anchorNodes: [], sendComment: view.sendComment },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('离开再回来')
  })
})

describe('comment card activation', () => {
  const comment = {
    id: 'card-a',
    author: 'reader',
    content: '评论正文',
    created_at: '2026-10-01T07:00:00Z',
    reply_to: 'node-a',
    anchor_quote: '重复引用',
  } as Block
  function cards() {
    return render(DocComments, {
      props: {
        topicId: `cards-${++serial}`,
        author: 'reader',
        comments: [comment],
        anchorNodes: [{ id: 'node-a', content: '重复引用和重复引用' }] as Block[],
        quoteState: () => 'ambiguous' as const,
      },
      global: { plugins: [createVuetify({ components, directives })] },
    })
  }
  it('opens a summary with its native button and leaves a child quote action independent', async () => {
    const view = cards()
    const article = screen.getByRole('article')
    const summary = within(article).getByRole('button')
    expect(summary.tagName).toBe('BUTTON')
    await fireEvent.click(summary)
    expect(view.emitted()['update:openId']).toEqual([['card-a']])
    const quote = within(article).getByRole('button', { name: '重复引用' })
    await fireEvent.keyDown(quote, { key: 'Enter' })
    expect(view.emitted()['update:openId']).toHaveLength(1)
    expect(quote.getAttribute('dir')).toBe('auto')
    expect(screen.getByText('这段引用出现多次，无法确定原选区')).toBeTruthy()
    await fireEvent.click(quote)
    expect(view.emitted()['locate-node']).toEqual([['node-a']])
    expect(view.emitted()['update:openId']).toHaveLength(2)
  })

  it('does not activate a card while dragging a selection in its body', async () => {
    const view = cards()
    const body = document.querySelector('[data-comment-body="card-a"]')!
    const selection = window.getSelection()!
    const range = document.createRange()
    range.selectNodeContents(body)
    selection.removeAllRanges()
    selection.addRange(range)
    try {
      await fireEvent.click(body)
      expect(view.emitted()['update:openId']).toBeUndefined()
    } finally {
      selection.removeAllRanges()
    }
    await fireEvent.click(body)
    expect(view.emitted()['update:openId']).toEqual([['card-a']])
  })

  it('expands a measured long body only on the active card', async () => {
    const view = cards()
    await view.rerender({ openId: 'card-a' })
    const body = document.querySelector<HTMLElement>('[data-comment-body="card-a"]')!
    Object.defineProperty(body, 'scrollHeight', { configurable: true, value: 1000 })
    await view.rerender({ comments: [{ ...comment, content: '评论正文。\n'.repeat(30) }] })
    const expand = await screen.findByRole('button', { name: '展开全文' })
    await fireEvent.click(expand)
    expect(body.classList.contains('is-expanded')).toBe(true)
    expect(view.emitted()['update:openId']).toBeUndefined()
    await fireEvent.click(screen.getByRole('button', { name: '收起全文' }))
    expect(body.classList.contains('is-expanded')).toBe(false)
  })

  it('reports the missing host sender rather than showing successful posting', async () => {
    cards()
    const input = await open()
    await fireEvent.update(input, '保留原稿')
    expect((screen.getByRole('button', { name: /^评论$/ }) as HTMLButtonElement).disabled).toBe(true)
    await fireEvent.keyDown(input, { key: 'Enter' })
    await screen.findByText('当前宿主未提供评论发送接口')
    expect(input.value).toBe('保留原稿')
  })
})

it('returns keyboard focus to the comment list and preserves each thread reply draft', async () => {
  const comments = ['Alice', 'Bob'].map((author, index) => ({
    id: `thread-${index}`,
    author,
    content: `Comment ${index}`,
    created_at: '2026-10-01T07:00:00Z',
    reply_to: null,
    anchor_quote: null,
  })) as Block[]
  const threadState: DocThreadState = {
    threads: Object.fromEntries(
      comments.map((comment) => [comment.id, { comment, revision: 1, state: 'open', anchor: null, replies: [] }])
    ),
    errors: {},
    busy: false,
    unknown: null,
  }
  const threadActions: DocThreadActions = {
    load: vi.fn(async () => undefined),
    reply: vi.fn(async () => undefined),
    resolve: vi.fn(async () => undefined),
    reopen: vi.fn(async () => undefined),
    recover: vi.fn(async () => undefined),
  }
  render(DocComments, {
    props: {
      topicId: `thread-drafts-${++serial}`,
      author: 'reader',
      comments,
      anchorNodes: [],
      threadState,
      threadActions,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  const first = screen.getByRole('button', { name: /^Alice ·/ })
  await fireEvent.click(first)
  await fireEvent.update(screen.getByRole('textbox', { name: '回复' }), 'Alice thread draft')
  await fireEvent.click(screen.getByRole('button', { name: '全部评论' }))
  await waitFor(() => expect(document.activeElement).toBe(first))
  await fireEvent.click(screen.getByRole('button', { name: /^Bob ·/ }))
  expect((screen.getByRole('textbox', { name: '回复' }) as HTMLTextAreaElement).value).toBe('')
  await fireEvent.update(screen.getByRole('textbox', { name: '回复' }), 'Bob thread draft')
  await fireEvent.click(screen.getByRole('button', { name: '全部评论' }))
  await fireEvent.click(first)
  expect((screen.getByRole('textbox', { name: '回复' }) as HTMLTextAreaElement).value).toBe('Alice thread draft')
  expect(threadActions.reply).not.toHaveBeenCalled()
})
