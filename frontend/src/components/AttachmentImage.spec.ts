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
  return render(AttachmentImage, { props: { topicId: 'topic-a', path: 'uploads/a/图.png', ...props } })
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
