import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadFile } from './api'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('authenticated file download', () => {
  it('downloads bytes with the session token and original filename', async () => {
    localStorage.setItem('accessToken', 'test-download-token')
    const blob = new Blob(['document'])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => blob }))
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('需求 文档.pdf')
      expect(this.href).toBe('blob:test')
    })
    await downloadFile('/api/topics/t/attachments/raw?path=uploads/test.pdf', '需求 文档.pdf')
    expect(fetch).toHaveBeenCalledWith('/api/topics/t/attachments/raw?path=uploads/test.pdf&download=true', {
      headers: { Authorization: 'Bearer test-download-token' },
    })
    expect(create).toHaveBeenCalledWith(blob)
    expect(click).toHaveBeenCalledOnce()
  })

  it('reports authorization failures instead of saving an error page', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')
    await expect(downloadFile('/api/projects/p/file/raw?path=secret.txt', 'secret.txt')).rejects.toThrow('403')
    expect(click).not.toHaveBeenCalled()
  })
})
