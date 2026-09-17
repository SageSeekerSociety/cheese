import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const attachmentImageUrl = vi.fn()

vi.mock('../api', () => ({
  attachmentImageUrl: (...args: unknown[]) => attachmentImageUrl(...args),
}))

import AttachmentImage from './AttachmentImage.vue'

let created: string[] = []
let revoked: string[] = []

beforeEach(() => {
  vi.clearAllMocks()
  created = []
  revoked = []
  let n = 0
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
    const url = `blob:image-${++n}`
    created.push(url)
    return url
  })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation((url: string) => {
    revoked.push(url)
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function mount(props: Record<string, unknown>) {
  return render(AttachmentImage, {
    props: { topicId: 'topic-a', path: 'uploads/a/图.png', ...props },
    // 缩略图加载不出来时画的 <v-icon> 需要一个 Vuetify 实例才认得出来。
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('draws the bytes it fetched, not a bare URL the browser cannot authenticate', async () => {
  attachmentImageUrl.mockResolvedValue('blob:image-1')

  const { container } = mount({})

  await waitFor(() => expect(container.querySelector('img')).toBeTruthy())
  const img = container.querySelector('img')!
  expect(img.getAttribute('src')).toBe('blob:image-1')
  // 点开看原图走的是同一个 object URL，不是那个匿名地址。
  expect(container.querySelector('a')!.getAttribute('href')).toBe('blob:image-1')
  expect(attachmentImageUrl).toHaveBeenCalledWith('topic-a', 'uploads/a/图.png')
})

it('uses the file name as alt text so a picture that never loads still says what it was', async () => {
  attachmentImageUrl.mockResolvedValue('blob:image-1')

  mount({ path: 'uploads/a/成绩单.png' })

  await waitFor(() => expect(screen.getByAltText('成绩单.png')).toBeTruthy())
})

it('gives the previous picture back when the message is edited to another one', async () => {
  attachmentImageUrl.mockResolvedValueOnce('blob:image-1').mockResolvedValueOnce('blob:image-2')

  const { container, rerender } = mount({})
  await waitFor(() => expect(container.querySelector('img')!.getAttribute('src')).toBe('blob:image-1'))

  await rerender({ topicId: 'topic-a', path: 'uploads/a/另一张.png' })

  await waitFor(() => expect(container.querySelector('img')!.getAttribute('src')).toBe('blob:image-2'))
  // object URL 是页面持有的内存，不还就一直留着。
  expect(revoked).toEqual(['blob:image-1'])
})

it('gives the picture back when the message scrolls out of the timeline', async () => {
  attachmentImageUrl.mockResolvedValue('blob:image-1')

  const { container, unmount } = mount({})
  await waitFor(() => expect(container.querySelector('img')).toBeTruthy())

  unmount()

  expect(revoked).toEqual(['blob:image-1'])
})

it('says the picture failed instead of leaving a blank the reader reads as “no picture”', async () => {
  attachmentImageUrl.mockRejectedValue(new Error('图片加载失败（HTTP 401）'))

  const { container } = mount({})

  await waitFor(() => expect(screen.getByText('图片加载失败')).toBeTruthy())
  expect(container.querySelector('img')).toBeNull()
})

// 输入框里那张待发的缩略图：同一个组件、同一份字节，只是长得小，而且点不开
// （「看原图」是发出去之后的事）。
it('draws the composer thumbnail from the fetched bytes as well', async () => {
  attachmentImageUrl.mockResolvedValue('blob:image-1')

  const { container } = mount({ thumb: true })

  await waitFor(() => expect(container.querySelector('.im-thumb__img')).toBeTruthy())
  expect(container.querySelector('.im-thumb__img')!.getAttribute('src')).toBe('blob:image-1')
  expect(container.querySelector('a')).toBeNull()
  expect(attachmentImageUrl).toHaveBeenCalledWith('topic-a', 'uploads/a/图.png')
})

// 缩略图挂了也得占住那个方格：塌下去的时候待发条会整体跳一下，而且读者分不清
// 「这张图加载不出来」和「我还没选图」。
it('keeps the thumbnail the same size when its bytes never arrive', async () => {
  attachmentImageUrl.mockRejectedValue(new Error('图片加载失败（HTTP 401）'))

  const { container } = mount({ thumb: true })

  await waitFor(() => expect(container.querySelector('.im-thumb__failed')).toBeTruthy())
  expect(container.querySelector('.att-face')).toBeTruthy()
  expect(container.querySelector('img')).toBeNull()
})

it('gives the thumbnail back when it is taken out of the composer', async () => {
  attachmentImageUrl.mockResolvedValue('blob:image-1')

  const { container, unmount } = mount({ thumb: true })
  await waitFor(() => expect(container.querySelector('.im-thumb__img')).toBeTruthy())

  unmount()

  expect(revoked).toEqual(['blob:image-1'])
})
