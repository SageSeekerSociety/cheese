import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
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
    expect(input.value).toBe('等待期间的新草稿')
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
