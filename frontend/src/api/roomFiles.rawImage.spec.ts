/** 取图片不走 `request()`，所以它自己要先把快过期的 token 换掉：页面放了一阵再点开一张
 *  图，旧 token 已经不认了，服务器答 404，读者看到一张裂图。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./http')>()
  return {
    ...actual,
    // 刷新这一步本身由 `refreshSession` 去问服务器；这里只模拟它的结果：存下新 token。
    ensureFreshToken: vi.fn(async () => {
      if (actual.tokenExpiresWithin(actual.authToken(), 60_000)) localStorage.setItem('accessToken', 'fresh')
    }),
  }
})

import { rawImageUrl } from './roomFiles'

function jwtExpiringIn(seconds: number): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + seconds }))
  return `h.${payload}.s`
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) =>
      (init?.headers as Record<string, string>).Authorization === 'Bearer fresh'
        ? new Response(new Blob(['png']), { status: 200 })
        : new Response('{}', { status: 404 })
    )
  )
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:img')
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('rawImageUrl', () => {
  it('renews a token about to expire before it asks for the image', async () => {
    localStorage.setItem('accessToken', jwtExpiringIn(10))
    await expect(rawImageUrl('/api/projects/p1/file/raw?path=logo.png')).resolves.toBe('blob:img')
  })
})
