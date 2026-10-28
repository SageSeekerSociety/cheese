/** 取某一版的字节、下载某一版都不走 `request()`，所以它们自己要先把快过期的 token
 *  换掉：页面放了一阵再滚到一张表格卡，旧 token 已经不认了，卡片会静默地少掉
 *  「改了 N 格」；预览旁的提示条会说「比对失败」，读者没做错什么。下载那一步同理，
 *  版本列表是打开面板时取的；锚点还得挂进文档才点得动。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./http')>()
  // 刷新这一步本身由 `refreshSession` 去问服务器；这里只模拟它的结果：存下新 token。
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

/** 换成新令牌的那一次才答应；拿着旧令牌的请求一律 401。 */
function respondToFreshOnly(body: () => BodyInit): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) =>
      (init?.headers as Record<string, string>).Authorization === 'Bearer fresh'
        ? new Response(body(), { status: 200 })
        : new Response('{}', { status: 401 })
    )
  )
}

beforeEach(() => {
  respondToFreshOnly(() => new Blob(['旧正文']))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('roomFileRevisionBytes', () => {
  it('renews a token about to expire before it reads a revision', async () => {
    localStorage.setItem('accessToken', jwtExpiringIn(10))
    const bytes = await roomFileRevisionBytes('room', 'r1')
    expect(new TextDecoder().decode(bytes)).toBe('旧正文')
  })

  it('renews a token about to expire before it asks for a revision', async () => {
    localStorage.setItem('accessToken', jwtExpiringIn(10))
    respondToFreshOnly(() => new Uint8Array([1, 2, 3]))

    await expect(roomFileRevisionBytes('room', 'rev-1')).resolves.toBeInstanceOf(ArrayBuffer)
  })
})

describe('downloadRoomFileRevision', () => {
  it('renews a token about to expire before it downloads a revision', async () => {
    localStorage.setItem('accessToken', jwtExpiringIn(10))
    respondToFreshOnly(() => new Blob(['v2']))
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:revision')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const revision = { id: 'rev-1', path: 'docs/a.docx', seq: 2 } as RoomFileRevision
    await downloadRoomFileRevision('room', revision)

    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalled()
  })

  it('attaches the anchor to the document before clicking it', async () => {
    localStorage.setItem('accessToken', jwtExpiringIn(10))
    const clicked: { attached: boolean; download: string }[] = []
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:rev')
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ attached: document.body.contains(this), download: this.download })
    })
    await downloadRoomFileRevision('room', {
      id: 'r1',
      path: '样例/招募方案.docx',
      seq: 1,
      version: 'v1',
      size: 3,
      author: 'cheese-1',
      author_kind: 'agent',
      source: 'ai',
      note: null,
      editor_key: null,
      created_at: new Date().toISOString(),
    })
    expect(clicked).toHaveLength(1)
    expect(clicked[0]?.attached).toBe(true)
    // 名字随界面语言变（`revisionFileName`）；这里只钉它认得出是哪一版、什么后缀。
    expect(clicked[0]?.download).toContain('招募方案')
    expect(clicked[0]?.download).toContain('.docx')
  })
})
