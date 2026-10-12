import { createVuetify } from 'vuetify'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AttachmentVideo from './AttachmentVideo.vue'

import { setLocale } from '@/i18n'
import { ATTACHMENT_SOURCE } from '@/lib/attachmentSource'

const mediaUrl = vi.fn()

const source = {
  imageUrl: () => Promise.reject(new Error('not an image')),
  mediaUrl,
  documentPdf: () => Promise.reject(new Error('not a document')),
  fileBytes: () => Promise.reject(new Error('not a document')),
}

beforeEach(() => {
  setLocale('en')
  mediaUrl.mockResolvedValue('blob:screen')
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('AttachmentVideo', () => {
  it('loads only after a click and leaves playback to the person', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4' },
      global: { plugins: [createVuetify()], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })

    expect(mediaUrl).not.toHaveBeenCalled()
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(mediaUrl).toHaveBeenCalledWith('topic-a', 'library/screen.mp4'))
    const video = document.querySelector('video')
    expect(video).toBeTruthy()
    expect(video!.hasAttribute('autoplay')).toBe(false)
    expect(video!.hasAttribute('playsinline')).toBe(true)
    expect(video!.hasAttribute('controls')).toBe(true)
  })

  it('allows a failed authenticated read to be retried', async () => {
    mediaUrl.mockRejectedValueOnce(new Error('unavailable'))
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4' },
      global: { plugins: [createVuetify()], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })

    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy())
    expect(screen.getByText('Unable to load this video. Reload or download it')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    expect(mediaUrl).toHaveBeenCalledTimes(2)
  })

  it('releases URLs on replacement, stale completion and unmount', async () => {
    const revoke = vi.spyOn(URL, 'revokeObjectURL')
    let finish!: (url: string) => void
    mediaUrl.mockImplementationOnce(
      () =>
        new Promise<string>((resolve) => {
          finish = resolve
        })
    )
    const view = render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/old.mp4' },
      global: { plugins: [createVuetify()], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await view.rerender({ topicId: 'topic-a', path: 'library/new.mp4' })
    finish('blob:stale')
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:stale'))
    expect(document.querySelector('video')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    await view.rerender({ topicId: 'topic-a', path: 'library/next.mp4' })
    expect(revoke).toHaveBeenCalledWith('blob:screen')
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    view.unmount()
    expect(revoke.mock.calls.filter(([url]) => url === 'blob:screen')).toHaveLength(2)
    revoke.mockRestore()
  })

  it('keeps a download action when the browser cannot play it', async () => {
    const download = vi.fn()
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mov', onDownload: download },
      global: { plugins: [createVuetify()], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })

    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    await fireEvent.error(document.querySelector('video')!)
    expect(screen.getByText('This browser cannot play the video. Download it to watch')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: /Download screen.mov/ }))
    expect(download).toHaveBeenCalledOnce()
  })
})
