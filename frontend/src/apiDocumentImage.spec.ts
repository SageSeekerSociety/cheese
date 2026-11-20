import { afterEach, describe, expect, it, vi } from 'vitest'

import { documentImageUrl } from './api/projectDocuments'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('文档里引用的图', () => {
  it('带着读者的登录凭证按文档去取：读得了文档的人才拿得到', async () => {
    localStorage.setItem('accessToken', 'reader-token')
    const blob = new Blob(['png-bytes'], { type: 'image/png' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => blob }))
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:figure')

    const url = await documentImageUrl('doc-1', 'figures/耗时.png')

    expect(url).toBe('blob:figure')
    expect(fetch).toHaveBeenCalledWith('/api/documents/doc-1/image?path=figures%2F%E8%80%97%E6%97%B6.png', {
      headers: expect.objectContaining({ Authorization: 'Bearer reader-token' }),
    })
  })

  it('读不到时报错，而不是给一个指向错误页的地址', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    const create = vi.spyOn(URL, 'createObjectURL')

    await expect(documentImageUrl('doc-1', 'a.png')).rejects.toThrow('404')
    expect(create).not.toHaveBeenCalled()
  })
})
