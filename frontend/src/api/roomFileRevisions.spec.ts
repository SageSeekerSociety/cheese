/** 取某一版的字节、下载某一版都不走 `request()`，所以它们自己要先把快过期的 token
 *  换掉：页面放了一阵再滚到一张表格卡，旧 token 已经不认了，卡片会静默地少掉
 *  「改了 N 格」；下载那一步同理，版本列表是打开面板时取的。 */
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

import { downloadRoomFileRevision, type RoomFileRevision, roomFileRevisionBytes } from './roomFileRevisions'

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

it('renews a token about to expire before it downloads a revision', async () => {
  localStorage.setItem('accessToken', jwtExpiringIn(10))
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    (init?.headers as Record<string, string>).Authorization === 'Bearer fresh'
      ? new Response(new Blob(['v2']), { status: 200 })
      : new Response('{}', { status: 401 })
  )
  vi.stubGlobal('fetch', fetchMock)
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:revision')
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

  const revision = { id: 'rev-1', path: 'docs/a.docx', seq: 2 } as RoomFileRevision
  await downloadRoomFileRevision('room', revision)

  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(click).toHaveBeenCalled()
})
