/** 项目文件的 `/file/raw` 只认 Authorization：`<img src>` 直接挂地址是匿名请求，服务器
 *  答 404，读者看到一张裂图。这里的假服务器照那个规矩答：带头才给图。 */
import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRawImage } from './useRawImage'

const revoked: string[] = []
let made = 0

beforeEach(() => {
  localStorage.setItem('accessToken', 'tok')
  revoked.length = 0
  made = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization
      return auth === 'Bearer tok'
        ? new Response(new Blob(['png']), { status: 200 })
        : new Response('{}', { status: 404 })
    })
  )
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:img-${++made}`)
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation((u: string) => void revoked.push(u))
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('useRawImage', () => {
  it('shows an image the raw endpoint only serves to a signed-in reader', async () => {
    const scope = effectScope()
    const image = scope.run(() => useRawImage(() => '/api/projects/p1/file/raw?path=logo.png'))!
    await vi.waitFor(() => expect(image.src.value).toBe('blob:img-1'))
    expect(image.failed.value).toBe(false)
    scope.stop()
  })

  it('gives the previous image back when it changes, and on dispose', async () => {
    const url = ref('/api/projects/p1/file/raw?path=a.png')
    const scope = effectScope()
    const image = scope.run(() => useRawImage(() => url.value))!
    await vi.waitFor(() => expect(image.src.value).toBe('blob:img-1'))

    url.value = '/api/projects/p1/file/raw?path=b.png'
    await nextTick()
    await vi.waitFor(() => expect(image.src.value).toBe('blob:img-2'))
    expect(revoked).toEqual(['blob:img-1'])

    scope.stop()
    expect(revoked).toEqual(['blob:img-1', 'blob:img-2'])
  })

  it('says it failed when the server will not give the image', async () => {
    localStorage.clear()
    const scope = effectScope()
    const image = scope.run(() => useRawImage(() => '/api/projects/p1/file/raw?path=logo.png'))!
    await vi.waitFor(() => expect(image.failed.value).toBe(true))
    expect(image.src.value).toBe('')
    scope.stop()
  })
})
