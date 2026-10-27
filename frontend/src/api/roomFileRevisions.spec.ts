/** 读某一版的字节不走 `request()`，所以它自己要先把快过期的 token 换掉：打开一份文档就会
 *  自动去比两版，标签页放了一阵再打开，旧 token 已经不认了（这里回答 401），提示条就会说
 *  「比对失败」——读者没做错什么。下载那一版也一样，锚点还得挂进文档才点得动。 */
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

import { downloadRoomFileRevision, roomFileRevisionBytes } from './roomFileRevisions'

function jwtExpiringIn(seconds: number): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + seconds }))
  return `h.${payload}.s`
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) =>
      (init?.headers as Record<string, string>).Authorization === 'Bearer fresh'
        ? new Response(new Blob(['旧正文']), { status: 200 })
        : new Response('{}', { status: 401 })
    )
  )
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
})

describe('downloadRoomFileRevision', () => {
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
