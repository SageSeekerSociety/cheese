// 下载这一条路的契约：带凭证取字节、以原文件名存下、成了说一句、没成说一句而不落盘。
//
// 「说一句」在 `api/roomFiles.downloadFile` 里做（提示本身在 `lib/download`），所以
// 这几条断的是提示，而不是调用点各自那行红字——那是这一改动撤掉的东西。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const notices = vi.hoisted(() => [] as { level: string; text: string }[])
vi.mock('vuetify-sonner', () => ({
  toast: {
    success: (text: string) => notices.push({ level: 'success', text }),
    error: (text: string) => notices.push({ level: 'error', text }),
  },
}))

import { downloadFile } from './api'

import { setLocale } from '@/i18n'

beforeEach(() => {
  // 这几条断的是中文那两句话。
  setLocale('zh-CN')
  notices.length = 0
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('authenticated file download', () => {
  it('adds a query to a retained artifact file URL without changing the route', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['saved']) }))
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:artifact')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await downloadFile('/api/projects/p/artifacts/a/versions/v/file', 'report.txt')
    expect(fetch).toHaveBeenCalledWith('/api/projects/p/artifacts/a/versions/v/file?download=true', expect.anything())
  })

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
      headers: expect.objectContaining({ Authorization: 'Bearer test-download-token' }),
    })
    expect(create).toHaveBeenCalledWith(blob)
    expect(click).toHaveBeenCalledOnce()
  })

  it('存下一份之后说一句「已下载」', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['x']) }))
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:ok')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await downloadFile('/api/projects/p/file/raw?path=a.txt', 'a.txt')

    expect(notices).toEqual([{ level: 'success', text: '已下载至浏览器当前下载目录' }])
  })

  it('reports authorization failures instead of saving an error page', async () => {
    // 一个没有正文的 403（拿不出理由的那一种）。
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => null }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')

    // 说一句失败的话，不落盘：既不该点那条链接（会存下一个 403 页面），也不该再往
    // 上抛——调用点那行红字已经撤掉，抛出去没人接。
    await expect(downloadFile('/api/projects/p/file/raw?path=secret.txt', 'secret.txt')).resolves.toBeUndefined()

    expect(click).not.toHaveBeenCalled()
    expect(notices).toEqual([{ level: 'error', text: '下载失败（HTTP 403）' }])
  })

  it('请求根本没发出去时，用平台那句网络话，不用浏览器自己的英文串', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await downloadFile('/api/projects/p/file/raw?path=a.txt', 'a.txt')

    expect(notices).toEqual([{ level: 'error', text: '网络连接失败，没能下载，检查网络后重试' }])
  })

  it('服务器给了理由就说它那句，不退回通用的 HTTP 话', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ code: 403, message: '这份文件不在你能读的范围里' }),
      })
    )

    await downloadFile('/api/projects/p/file/raw?path=secret.txt', 'secret.txt')

    expect(notices).toEqual([{ level: 'error', text: '这份文件不在你能读的范围里' }])
  })

  it('拒绝的正文读不动（错误页、空体）时退回通用的 HTTP 话', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error('not json')
        },
      })
    )

    await downloadFile('/api/projects/p/file/raw?path=a.txt', 'a.txt')

    expect(notices).toEqual([{ level: 'error', text: '下载失败（HTTP 502）' }])
  })
})
