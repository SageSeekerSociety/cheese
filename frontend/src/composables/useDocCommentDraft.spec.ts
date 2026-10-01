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
const first = { anchorId: 'paragraph-a', quote: '第一段原文' }
const second = { anchorId: 'paragraph-b', quote: '第二段原文' }

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
    expect(Object.values(saved.drafts)).toEqual([{ target: first, text: '重新挂载后继续写' }])
    scope.stop()
  })
})
