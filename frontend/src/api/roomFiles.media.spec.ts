/** 播放地址那一次 range 请求的应答里就带着整段录屏的大小，顺手读出来；读不到就交 null，
 *  让卡片少写一段，而不是写一个 0 B。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { attachmentMediaUrl } from './roomFiles'

beforeEach(() => {
  localStorage.setItem('accessToken', 'token')
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('attachmentMediaUrl', () => {
  it('reads the whole size off the same response it signs the address with', async () => {
    // 声明签名，`mock.calls` 才是 (url, init) 的二元组，读得到 Range 头。
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => {
      return new Response(null, { status: 206, headers: { 'Content-Range': 'bytes 0-0/15519744' } })
    })
    vi.stubGlobal('fetch', fetchMock)

    const got = await attachmentMediaUrl('topic-a', 'library/screen.mp4')

    expect(got.bytes).toBe(15_519_744)
    expect(got.url).toContain('/topics/topic-a/attachments/raw')
    // 只要一个字节：地址签下来就够了，整段录屏由播放器自己按 range 流式读。
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect((init.headers as Record<string, string>).Range).toBe('bytes=0-0')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('says null instead of guessing when the server does not report a size', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(null, { status: 200 }))
    )

    await expect(attachmentMediaUrl('topic-a', 'library/screen.mp4')).resolves.toMatchObject({ bytes: null })
  })

  it('still reports the failure when this viewer may not read it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 403 }))
    )

    await expect(attachmentMediaUrl('topic-a', 'library/screen.mp4')).rejects.toThrow()
  })
})
