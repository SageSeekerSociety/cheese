import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
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

function url(value: string, bytes: number | null = 15_519_744) {
  return { url: value, bytes }
}

beforeEach(() => {
  setLocale('en')
  mediaUrl.mockResolvedValue(url('/api/topics/topic-a/attachments/raw?path=library%2Fscreen.mp4'))
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('AttachmentVideo', () => {
  it('loads only after a click and leaves playback to the person', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4' },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })

    expect(mediaUrl).not.toHaveBeenCalled()
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(mediaUrl).toHaveBeenCalledWith('topic-a', 'library/screen.mp4'))
    const video = document.querySelector('video')
    expect(video).toBeTruthy()
    expect(video!.hasAttribute('autoplay')).toBe(false)
    expect(video!.hasAttribute('playsinline')).toBe(true)
    // 自绘控制条：画面本身不再是浏览器的控件，键盘和读屏由卡片自己接。
    expect(video!.hasAttribute('controls')).toBe(false)
    expect(video!.getAttribute('preload')).toBe('metadata')
  })

  it('annotates the moment on screen, and only once the player is there', async () => {
    const ui = render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4', canLocate: true },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    // Not opened yet: there is no "now" to point at, so the entry is off instead of handing over 00:00.
    const locate = screen.getByTestId('video-locate') as HTMLButtonElement
    expect(locate.disabled).toBe(true)
    // 灰着的按钮收不到悬停：为什么灰写在包着它的那一层上。
    expect(locate.parentElement?.getAttribute('title')).toBe('Open the video first, then annotate a moment of it')

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
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })

    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy())
    expect(screen.getByText('Unable to load this video. Reload or download it')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    expect(mediaUrl).toHaveBeenCalledTimes(2)
  })

  it('does not show the previous video once the attachment changes', async () => {
    let finish!: (value: { url: string; bytes: number | null }) => void
    mediaUrl.mockImplementationOnce(
      () =>
        new Promise<{ url: string; bytes: number | null }>((resolve) => {
          finish = resolve
        })
    )
    const view = render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/old.mp4' },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await view.rerender({ topicId: 'topic-a', path: 'library/new.mp4' })
    finish(url('/api/old'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open video' })).toBeTruthy())
    expect(document.querySelector('video')).toBeNull()
  })

  it('gives a portrait recording a portrait box, not a 16:9 one', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/IMG_0001.MOV' },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
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
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })

    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    await fireEvent.error(document.querySelector('video')!)
    expect(screen.getByText('This browser cannot play the video. Download it to watch')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy()
    // 放不了就没有控制条：一颗按下去没反应的播放键比没有更糟。
    expect(screen.queryByTestId('video-play')).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: /Download screen.mov/ }))
    expect(download).toHaveBeenCalledOnce()
  })

  it('draws its own controls, and reports position and duration on the bar', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4' },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    const video = await waitFor(() => {
      const el = document.querySelector('video')
      expect(el).toBeTruthy()
      return el as HTMLVideoElement
    })
    // 元数据到手：控制条上念出 0:00 / 1:12。
    Object.defineProperty(video, 'duration', { value: 72, configurable: true })
    Object.defineProperty(video, 'currentTime', { value: 0, writable: true, configurable: true })
    await fireEvent(video, new Event('loadedmetadata'))
    expect(screen.getByText('0:00 / 1:12')).toBeTruthy()

    // 拖进度条 = 设 currentTime，不是重新下一遍。
    const bar = screen.getByTestId('video-bar') as HTMLInputElement
    expect(bar.getAttribute('max')).toBe('72')
    await fireEvent.input(bar, { target: { value: '28' } })
    expect(video.currentTime).toBe(28)
  })

  it('names the file, its length and its size under the picture', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4' },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    // 大小来自签发地址那次响应的 Content-Range，不额外发一次请求。
    expect(screen.getByText(/screen\.mp4 · 14\.80 MB/)).toBeTruthy()
  })

  it('takes the keyboard only while the card itself has focus', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4' },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    const video = await waitFor(() => {
      const el = document.querySelector('video')
      expect(el).toBeTruthy()
      return el as HTMLVideoElement
    })
    Object.defineProperty(video, 'duration', { value: 72, configurable: true })
    Object.defineProperty(video, 'currentTime', { value: 30, writable: true, configurable: true })
    // jsdom 的 HTMLMediaElement 不实现 play()；这一段只关心按键落到谁身上。
    const play = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(video, 'play', { value: play, configurable: true })
    const card = document.querySelector('.video-card') as HTMLElement

    // 焦点在卡片上：左右键前后 5 秒。
    await fireEvent.keyDown(card, { key: 'ArrowRight' })
    expect(video.currentTime).toBe(35)
    await fireEvent.keyDown(card, { key: 'ArrowLeft' })
    expect(video.currentTime).toBe(30)
    await fireEvent.keyDown(card, { key: ' ' })
    expect(play).toHaveBeenCalledOnce()

    // 焦点在别处：这一张卡接不到那个空格——一次都不该有人替读者暂停别处的东西。
    // （监听器挂在卡片上而不是 window 上，这一条就是钉那件事。）
    play.mockClear()
    await fireEvent.keyDown(document.body, { key: ' ' })
    expect(play).not.toHaveBeenCalled()
  })

  it('cycles the playback speed through the card', async () => {
    render(AttachmentVideo, {
      props: { topicId: 'topic-a', path: 'library/screen.mp4' },
      global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
    await waitFor(() => expect(document.querySelector('video')).toBeTruthy())
    const rate = screen.getByTestId('video-rate')
    expect(rate.textContent?.trim()).toBe('1×')
    await fireEvent.click(rate)
    expect(screen.getByTestId('video-rate').textContent?.trim()).toBe('1.25×')
  })

  it('does not fetch the opening frame when the reader is on a metered link', async () => {
    const original = navigator.connection
    Object.defineProperty(navigator, 'connection', { value: { saveData: true }, configurable: true })
    try {
      render(AttachmentVideo, {
        props: { topicId: 'topic-a', path: 'library/screen.mp4' },
        global: { plugins: [createVuetify({ components, directives })], provide: { [ATTACHMENT_SOURCE as symbol]: source } },
      })
      await fireEvent.click(screen.getByRole('button', { name: 'Open video' }))
      const video = await waitFor(() => {
        const el = document.querySelector('video')
        expect(el).toBeTruthy()
        return el as HTMLVideoElement
      })
      Object.defineProperty(video, 'duration', { value: 72, configurable: true })
      Object.defineProperty(video, 'videoWidth', { value: 1280 })
      Object.defineProperty(video, 'videoHeight', { value: 720 })
      // 省流时不去 seek 到 0.1 秒取起播帧：currentTime 因此一次都不该被动过。
      let touched = false
      Object.defineProperty(video, 'currentTime', {
        get: () => 0,
        set: () => {
          touched = true
        },
        configurable: true,
      })
      await fireEvent(video, new Event('loadeddata'))
      expect(touched).toBe(false)
    } finally {
      Object.defineProperty(navigator, 'connection', { value: original, configurable: true })
    }
  })
})
