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
  mediaUrl.mockResolvedValue('/api/topics/topic-a/attachments/raw?path=library%2Fscreen.mp4')
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

  it('annotates the moment on screen, and only once the player is there', async () => {
    const ui = render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4', canLocate: true },
      global: { plugins: [createVuetify()], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    // Not opened yet: there is no "now" to point at, so the entry is off instead of handing over 00:00.
    const locate = screen.getByTestId('video-locate') as HTMLButtonElement
    expect(locate.disabled).toBe(true)

    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    const video = await waitFor(() => {
      const el = document.querySelector('video')
      expect(el).toBeTruthy()
      return el as HTMLVideoElement
    })
    Object.defineProperty(video, 'currentTime', { value: 90.7, configurable: true })
    expect((screen.getByTestId('video-locate') as HTMLButtonElement).disabled).toBe(false)
    await fireEvent.click(screen.getByTestId('video-locate'))
    expect(ui.emitted().locate).toEqual([[{ seconds: 90, timestamp: '01:30' }]])
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

  it('does not show the previous video once the attachment changes', async () => {
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
    finish('/api/old')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open video' })).toBeTruthy())
    expect(document.querySelector('video')).toBeNull()
  })

  it('gives a portrait recording a portrait box, not a 16:9 one', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/IMG_0001.MOV' },
      global: { plugins: [createVuetify()], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    const video = document.querySelector('video')!
    // An iPhone screen recording: 1179 × 2556.
    Object.defineProperty(video, 'videoWidth', { value: 1179 })
    Object.defineProperty(video, 'videoHeight', { value: 2556 })
    await fireEvent(video, new Event('loadedmetadata'))

    const ratio = Number(video.style.getPropertyValue('--video-ratio'))
    expect(ratio).toBeCloseTo(1179 / 2556, 4)
    expect(ratio).toBeLessThan(1)
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
