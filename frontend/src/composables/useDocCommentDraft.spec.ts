import { effectScope, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useDocCommentDraft } from './useDocCommentDraft'

let serial = 0
function fixture() {
  const topic = ref(`selection-draft-${++serial}`)
  const author = ref('reader')
  const send = vi.fn(async (): Promise<void> => undefined)
  const posted = vi.fn()
  const scope = effectScope()
  const draft = scope.run(() =>
    useDocCommentDraft(
      () => topic.value,
      () => author.value,
      send,
      posted
    )
  )!
  return { ...draft, topic, author, send, posted, scope }
}
const first = { quote: '第一段原文', from: 1, to: 6, rel: null }
const second = { quote: '第二段原文', from: 9, to: 14, rel: null }

describe('selection-specific comment drafts', () => {
  it('retains each unsent selection and isolates authors', () => {
    const d = fixture()
    d.open(first)
    d.text.value = '第一段草稿'
    d.open(second)
    expect(d.text.value).toBe('')
    d.text.value = '第二段草稿'
    d.open(first)
    expect(d.text.value).toBe('第一段草稿')
    d.author.value = 'another-reader'
    d.open(first)
    expect(d.text.value).toBe('')
    d.author.value = 'reader'
    expect(d.text.value).toBe('第一段草稿')
    d.scope.stop()
  })

  it('does not clear another selection when the original receipt arrives', async () => {
    const d = fixture()
    let finish!: () => void
    d.send.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    d.open(first)
    d.text.value = '已发送'
    const operation = d.submit()
    d.open(second)
    d.text.value = '仍在写'
    finish()
    await operation
    expect(d.draft.value).toEqual(second)
    expect(d.text.value).toBe('仍在写')
    d.open(first)
    expect(d.text.value).toBe('')
    d.scope.stop()
  })

  it('does not emit a refresh from an unmounted sender', async () => {
    const d = fixture()
    let finish!: () => void
    d.send.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve
        })
    )
    d.open(first)
    d.text.value = '已发送'
    const operation = d.submit()
    d.scope.stop()
    finish()
    await operation
    expect(d.posted).not.toHaveBeenCalled()
  })

  it('persists edits from a later mount of the same topic', () => {
    const d = fixture()
    d.open(first)
    d.text.value = '第一次写'
    d.scope.stop()
    const scope = effectScope()
    const next = scope.run(() =>
      useDocCommentDraft(
        () => d.topic.value,
        () => 'reader',
        d.send,
        vi.fn()
      )
    )!
    next.text.value = '重新挂载后继续写'
    const saved = JSON.parse(
      sessionStorage.getItem(`cheese:doc-comments:${JSON.stringify(['reader', d.topic.value])}`)!
    )
    // The shared-document positions do not outlive the page; the words and where they were do.
    expect(Object.values(saved.drafts)).toEqual([
      { target: { quote: first.quote, from: first.from, to: first.to }, text: '重新挂载后继续写' },
    ])
    scope.stop()
  })
})

describe('closing is distinct from discarding comment drafts', () => {
  it('keeps target/text through close, remount and resume, and discards only explicitly', () => {
    const d = fixture()
    d.open(first)
    d.text.value = '保留'
    expect(d.close()).toBe(true)
    expect(d.draft.value).toBeNull()
    expect(d.hasDraft.value).toBe(true)
    expect(d.target.value).toEqual(first)
    const saved = JSON.parse(
      sessionStorage.getItem(`cheese:doc-comments:${JSON.stringify(['reader', d.topic.value])}`)!
    )
    expect(saved.hidden).toBe(true)
    d.scope.stop()
    const scope = effectScope()
    const next = scope.run(() =>
      useDocCommentDraft(
        () => d.topic.value,
        () => 'reader',
        d.send,
        vi.fn()
      )
    )!
    expect(next.draft.value).toBeNull()
    next.open(first)
    expect(next.text.value).toBe('保留')
    next.cancel()
    expect(next.hasDraft.value).toBe(false)
    scope.stop()
  })

  it('guards close/discard across targets while another target is sending', async () => {
    const d = fixture()
    let finish!: () => void
    d.send.mockImplementation(
      () =>
        new Promise<void>((done) => {
          finish = done
        })
    )
    d.open(first)
    d.text.value = '已送出'
    const operation = d.submit()
    d.open(second)
    d.text.value = '另一处草稿'
    expect(d.sending.value).toBe(false)
    expect(d.busy.value).toBe(true)
    expect(d.close()).toBe(false)
    d.cancel()
    expect(d.draft.value).toEqual(second)
    finish()
    await operation
    expect(d.busy.value).toBe(false)
    expect(d.text.value).toBe('另一处草稿')
    d.scope.stop()
  })
})

describe('asking the AI teammate about a selection', () => {
  it('starts the comment with the mention, and sends it with the mention', async () => {
    const d = fixture()
    d.open(first, '@芝士 ')
    expect(d.text.value).toBe('@芝士 ')
    d.text.value += '这个数字是怎么来的'
    await d.submit()
    expect(d.send).toHaveBeenCalledWith(d.topic.value, '@芝士 这个数字是怎么来的', first)
    d.scope.stop()
  })

  it('keeps what was already written on the same selection', () => {
    const d = fixture()
    d.open(first)
    d.text.value = '写了一半的评论'
    d.open(first, '@芝士 ')
    expect(d.text.value).toBe('写了一半的评论')
    d.scope.stop()
  })
})
