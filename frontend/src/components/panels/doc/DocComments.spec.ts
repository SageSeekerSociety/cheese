// @vitest-environment jsdom
// 评论栏里的内容：写到一半的评论不丢、回车才发、输入法的回车不算；一串评论一张卡，
// 点卡片是看它（正文跟着滚过去），拖着选字不算；长的对话中间先收起来；AI 队友在答时
// 卡上说它到了哪一步；回复发出去了才清空，失败了字还在。
import type { CommentSpot } from '../../../lib/docCommentSpots'
import type { DocThread, DocThreadActions, DocThreadState, ThreadPlace } from '../../../lib/docThreadTypes'

import { defineComponent, h, reactive, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DocComments from './DocComments.vue'

import { setLocale } from '@/i18n'

let serial = 0
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const spot: CommentSpot = { quote: '五百万行', from: 5, to: 9, rel: null }

function thread(id: string, over: Partial<DocThread> = {}): DocThread {
  return {
    comment: {
      id,
      author: 'alice',
      content: `评论 ${id}`,
      created_at: '2026-10-01T07:00:00Z',
      anchor_quote: '五百万行',
    } as DocThread['comment'],
    revision: 1,
    state: 'open',
    replies: [],
    ...over,
  }
}
function reply(n: number, author = 'bob') {
  return {
    sequence: n,
    comment: { id: `r${n}`, author, content: `回复 ${n}`, created_at: '2026-10-01T07:00:00Z' },
  } as DocThread['replies'][number]
}

function mount(
  options: {
    send?: ReturnType<typeof vi.fn>
    threads?: DocThread[]
    actions?: Partial<DocThreadActions>
    place?: ThreadPlace
    filter?: 'open' | 'resolved'
    topicId?: string
  } = {}
) {
  const topicId = ref(options.topicId ?? `comments-${++serial}`)
  const openId = ref<string | null>(null)
  const state = reactive<DocThreadState>({
    threads: options.threads ?? [],
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
    ...options.actions,
  }
  const located: string[] = []
  const send = options.send ?? vi.fn(async () => undefined)
  const comments = ref<InstanceType<typeof DocComments> | null>(null)
  const view = render(
    defineComponent({
      setup() {
        return () =>
          h(DocComments, {
            ref: comments,
            topicId: topicId.value,
            author: 'reader',
            threadState: state,
            threadActions: actions,
            sendComment: options.send === null ? undefined : send,
            openId: openId.value,
            placeOf: () => (options.place === undefined ? 'marked' : options.place),
            agentName: '芝士',
            mentionNames: {},
            nameOf: (handle: string) => handle,
            writable: true,
            filter: options.filter ?? 'open',
            'onUpdate:openId': (id: string | null) => (openId.value = id),
            onLocate: (id: string) => located.push(id),
          })
      },
    }),
    { global: { plugins: [createVuetify()] } }
  )
  async function write(at: CommentSpot = spot) {
    comments.value!.open(at)
    return (await screen.findByLabelText('写评论')) as HTMLTextAreaElement
  }
  return { ...view, topicId, openId, state, actions, located, send, write }
}
function pending() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

describe('writing a comment', () => {
  it.each([{ isComposing: true }, { keyCode: 229 }, { shiftKey: true }])(
    'does not post an IME or newline Enter: %o',
    async (options) => {
      const view = mount()
      const input = await view.write()
      await fireEvent.update(input, '正在输入中文')
      await fireEvent.keyDown(input, { key: 'Enter', ...options })
      expect(view.send).not.toHaveBeenCalled()
      expect(input.value).toBe('正在输入中文')
    }
  )

  it('posts on Enter, about the selected words, and closes the draft once it is sent', async () => {
    const view = mount()
    const input = await view.write()
    await fireEvent.update(input, '这个数字要再核实')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(view.send).toHaveBeenCalledWith(view.topicId.value, '这个数字要再核实', spot))
    await waitFor(() => expect(screen.queryByLabelText('写评论')).toBeNull())
  })

  it('keeps the text after a failed send and sends it on retry', async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(undefined)
    const view = mount({ send })
    const input = await view.write()
    await fireEvent.update(input, '不能丢掉')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await screen.findByText('offline')
    expect(input.value).toBe('不能丢掉')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(screen.queryByLabelText('写评论')).toBeNull())
    expect(send).toHaveBeenCalledTimes(2)
  })

  it('does not clear what was typed while an earlier send was on its way', async () => {
    const receipt = pending()
    const view = mount({ send: vi.fn(() => receipt.promise) })
    const input = await view.write()
    await fireEvent.update(input, '已送出')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await fireEvent.update(input, '等待期间的新草稿')
    receipt.resolve()
    await waitFor(() => expect(view.send).toHaveBeenCalledTimes(1))
    await Promise.resolve()
    expect((screen.getByLabelText('写评论') as HTMLTextAreaElement).value).toBe('等待期间的新草稿')
  })

  it('keeps each topic’s draft, also across a remount', async () => {
    const view = mount()
    const input = await view.write()
    await fireEvent.update(input, '离开再回来')
    view.topicId.value = `${view.topicId.value}-other`
    await waitFor(() => expect(screen.queryByLabelText('写评论')).toBeNull())
    const topic = view.topicId.value.replace(/-other$/, '')
    view.unmount()
    mount({ topicId: topic })
    expect((screen.getByLabelText('写评论') as HTMLTextAreaElement).value).toBe('离开再回来')
  })

  it('says it cannot send when there is nowhere to send to, and keeps the text', async () => {
    const view = mount({ send: null as never })
    const input = await view.write()
    await fireEvent.update(input, '保留原稿')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await screen.findByText('无法发送评论')
    expect(input.value).toBe('保留原稿')
  })
})

describe('a thread’s card', () => {
  it('is looked at when clicked, and the text is asked to show its words; dragging a selection is not a click', async () => {
    const view = mount({ threads: [thread('a')] })
    const card = screen.getByRole('article')
    const body = within(card).getByText('评论 a')
    const selection = window.getSelection()!
    const range = document.createRange()
    range.selectNodeContents(body)
    selection.removeAllRanges()
    selection.addRange(range)
    await fireEvent.click(body)
    expect(view.openId.value).toBeNull()
    selection.removeAllRanges()
    await fireEvent.click(body)
    expect(view.openId.value).toBe('a')
    expect(view.located).toEqual(['a'])
  })

  it('shows the start and the latest of a long conversation, and the rest on request', async () => {
    mount({ threads: [thread('a', { replies: [1, 2, 3, 4, 5].map((n) => reply(n)) })] })
    expect(screen.getByText('评论 a')).toBeTruthy()
    expect(screen.queryByText('回复 1')).toBeNull()
    expect(screen.getByText('回复 4')).toBeTruthy()
    expect(screen.getByText('回复 5')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '展开 3 条回复' }))
    expect(screen.getByText('回复 1')).toBeTruthy()
  })

  it('says how far the AI teammate has got while it answers', async () => {
    const view = mount({ threads: [thread('a')] })
    view.state.activity.a = { state: 'queued' }
    expect(await screen.findByText('芝士正忙，空闲后自动回复')).toBeTruthy()
    view.state.activity.a = { state: 'working', tool: 'cheese_doc_edit' }
    expect(await screen.findByText('芝士正在修改文档…')).toBeTruthy()
    delete view.state.activity.a
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
  })

  it('sends a reply on Enter and clears it only once it is in; a failed one stays', async () => {
    const replyTo = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(undefined)
    const view = mount({ threads: [thread('a')], actions: { reply: replyTo } })
    await fireEvent.click(screen.getByText('评论 a'))
    const input = screen.getByRole('textbox', { name: '回复' }) as HTMLTextAreaElement
    await fireEvent.update(input, '同意')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(replyTo).toHaveBeenCalledWith('a', '同意'))
    expect(input.value).toBe('同意')
    await fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(input.value).toBe(''))
    expect(view.actions.reply).toHaveBeenCalledTimes(2)
  })

  it('lists resolved threads apart from open ones', () => {
    mount({ threads: [thread('a'), thread('b', { state: 'resolved' })], filter: 'resolved' })
    expect(screen.queryByText('评论 a')).toBeNull()
    expect(screen.getByText('评论 b')).toBeTruthy()
  })

  it('still goes to where rewritten words were, and only nowhere-to-be-found words cannot be gone to', async () => {
    const view = mount({ threads: [thread('a')], place: 'placed' })
    await fireEvent.click(screen.getByRole('button', { name: '五百万行' }))
    expect(view.located).toEqual(['a'])
    cleanup()
    mount({ threads: [thread('a')], place: null })
    expect((screen.getByRole('button', { name: '五百万行' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
