import { afterEach, describe, expect, it, vi } from 'vitest'

import { attachmentImageUrl } from './api'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('attachment images', () => {
  it('fetches the bytes with the session token, because the endpoint takes no anonymous reader', async () => {
    localStorage.setItem('accessToken', 'test-image-token')
    const blob = new Blob(['png-bytes'], { type: 'image/png' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => blob }))
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:image')

    const url = await attachmentImageUrl('topic-a', 'uploads/abc/截图 1.png')

    expect(url).toBe('blob:image')
    // header 是重点：<img src> 带不了它，所以这一步只能由脚本发。
    expect(fetch).toHaveBeenCalledWith(
      '/api/topics/topic-a/attachments/raw?path=uploads%2Fabc%2F%E6%88%AA%E5%9B%BE%201.png',
      { headers: { Authorization: 'Bearer test-image-token' } }
    )
    expect(create).toHaveBeenCalledWith(blob)
  })

  it('fails rather than handing back a URL to an error page', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    const create = vi.spyOn(URL, 'createObjectURL')

    await expect(attachmentImageUrl('topic-a', 'uploads/abc/x.png')).rejects.toThrow('401')
    expect(create).not.toHaveBeenCalled()
  })
})
