/** 取某一版的字节不走 `request()`，所以它自己要先把快过期的 token 换掉：页面放了一
 *  阵再滚到一张表格卡，旧 token 已经不认了，卡片会静默地少掉「改了 N 格」。 */
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('./http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./http')>()
  return {
    ...actual,
    ensureFreshToken: vi.fn(async () => {
      if (actual.tokenExpiresWithin(actual.authToken(), 60_000)) localStorage.setItem('accessToken', 'fresh')
    }),
  }
})

import { roomFileRevisionBytes } from './roomFileRevisions'

function jwtExpiringIn(seconds: number): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + seconds }))
  return `h.${payload}.s`
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

it('renews a token about to expire before it asks for a revision', async () => {
  localStorage.setItem('accessToken', jwtExpiringIn(10))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) =>
      (init?.headers as Record<string, string>).Authorization === 'Bearer fresh'
        ? new Response(new Uint8Array([1, 2, 3]), { status: 200 })
        : new Response('{}', { status: 401 })
    )
  )

  await expect(roomFileRevisionBytes('room', 'rev-1')).resolves.toBeInstanceOf(ArrayBuffer)
})
