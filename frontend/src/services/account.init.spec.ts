// 启动时的会话恢复：过期的访问令牌必须先续签，再宣布登录。
//
// 线上现象：每次进页面 console 都有一条 `GET /api/notifications/unread-count
// → 401`，铃铛未读数永远是 0。后端那条路由是有的（contract 测试里带合法令牌是
// 200），401 是货真价实的凭证被拒——访问令牌只活 15 分钟，而 init() 以前不看
// exp，直接把 loggedIn 置 true，铃铛就顶着一个死令牌开打。
//
// 页面上别的请求（topics/blocks/projects）在这个部署里不鉴权，所以只有铃铛会
// 暴露出来。这份测试盯的就是「loggedIn 翻 true 时，手里是不是一个活令牌」。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getCurrentUser = vi.fn()
// The refresh endpoint, answered the way the server would.
const refresh = vi.fn()

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  },
}))

// Stands in for the browser's channel between tabs. A fresh one per test: the
// real one would carry a message from one test's service into the next's.
let tabs: Set<FakeChannel>
class FakeChannel {
  private handlers: ((event: MessageEvent) => void)[] = []
  constructor(readonly name: string) {
    tabs.add(this)
  }
  addEventListener(_type: 'message', handler: (event: MessageEvent) => void) {
    this.handlers.push(handler)
  }
  postMessage(data: unknown) {
    for (const other of tabs) {
      if (other !== this && other.name === this.name) {
        queueMicrotask(() => other.handlers.forEach((h) => h({ data } as MessageEvent)))
      }
    }
  }
  close() {
    tabs.delete(this)
  }
}

function answer(status: number, body: unknown = {}): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as unknown as Response
}

// 一个 HS256 形状的假令牌：只有中段 payload 会被读，签名无所谓。
function jwtWithExp(expSeconds: number): string {
  const payload = btoa(JSON.stringify({ sub: '1', type: 'access', exp: expSeconds }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
  return `header.${payload}.signature`
}

const USER = { id: 1, username: 'alice', nickname: '爱丽丝' }

async function freshService() {
  vi.resetModules()
  const mod = await import('./account')
  return mod.default
}

beforeEach(() => {
  localStorage.clear()
  refresh.mockReset()
  getCurrentUser.mockReset()
  getCurrentUser.mockResolvedValue({ data: { user: USER } })
  tabs = new Set()
  vi.stubGlobal('BroadcastChannel', FakeChannel)
  vi.stubGlobal('fetch', async (url: string) => {
    if (String(url).includes('refresh-token')) return refresh()
    throw new Error(`unexpected request ${url}`)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('isTokenExpired', () => {
  it('认得出还没到期 / 已经到期', async () => {
    const { isTokenExpired } = await import('./account')
    const now = 1_800_000_000_000
    expect(isTokenExpired(jwtWithExp(now / 1000 + 600), now)).toBe(false)
    expect(isTokenExpired(jwtWithExp(now / 1000 - 1), now)).toBe(true)
  })

  it('快到期的（余量内）也算过期，免得请求发出去令牌就死在路上', async () => {
    const { isTokenExpired } = await import('./account')
    const now = 1_800_000_000_000
    expect(isTokenExpired(jwtWithExp(now / 1000 + 5), now)).toBe(true)
  })

  it('解不开的令牌一律当过期', async () => {
    const { isTokenExpired } = await import('./account')
    expect(isTokenExpired('not-a-jwt')).toBe(true)
    expect(isTokenExpired('')).toBe(true)
  })
})

describe('AccountService.init()', () => {
  it('令牌还新鲜：不去续签，直接登录', async () => {
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 + 600))
    localStorage.setItem('user', JSON.stringify(USER))

    const account = await freshService()
    await account.init()

    expect(refresh).not.toHaveBeenCalled()
    expect(account.loggedIn).toBe(true)
  })

  it('令牌已过期：先续签，拿到新令牌后才 loggedIn=true', async () => {
    const stale = jwtWithExp(Date.now() / 1000 - 60)
    const fresh = jwtWithExp(Date.now() / 1000 + 900)
    localStorage.setItem('accessToken', stale)
    localStorage.setItem('user', JSON.stringify(USER))
    refresh.mockResolvedValue(answer(200, { data: { accessToken: fresh, user: USER } }))

    const account = await freshService()
    await account.init()

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(account.loggedIn).toBe(true)
    // 关键：loggedIn 翻 true 的那一刻，手里必须是新令牌，不能还是那个死的。
    expect(account.accessToken).toBe(fresh)
    expect(localStorage.getItem('accessToken')).toBe(fresh)
  })

  it('续签被服务端拒了：登出，而不是顶着假登录态一路撒 401', async () => {
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 - 60))
    localStorage.setItem('user', JSON.stringify(USER))
    const account = await freshService()
    refresh.mockResolvedValue(answer(401))

    await account.init()

    expect(account.loggedIn).toBe(false)
    expect(account.accessToken).toBeNull()
    expect(localStorage.getItem('accessToken')).toBeNull()
  })

  it('续签是网络错误：不登录，但保留本地会话，下次进页面再续', async () => {
    const stale = jwtWithExp(Date.now() / 1000 - 60)
    localStorage.setItem('accessToken', stale)
    localStorage.setItem('user', JSON.stringify(USER))
    refresh.mockRejectedValue(new TypeError('Failed to fetch'))

    const account = await freshService()
    await account.init()

    // 没有活令牌就不宣布登录——否则还是会撒 401。
    expect(account.loggedIn).toBe(false)
    // 但登录还没过期，别拿网络抖动当登出理由。
    expect(localStorage.getItem('accessToken')).toBe(stale)
    expect(localStorage.getItem('user')).not.toBeNull()
  })

  it('续签返回体缺 accessToken/user 也算失败', async () => {
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 - 60))
    localStorage.setItem('user', JSON.stringify(USER))
    refresh.mockResolvedValue(answer(200, { data: { accessToken: '', user: null } }))

    const account = await freshService()
    await account.init()

    expect(account.loggedIn).toBe(false)
  })

  it('本来就没登录过：什么都不做，也不去续签', async () => {
    const account = await freshService()
    await account.init()

    expect(refresh).not.toHaveBeenCalled()
    expect(account.loggedIn).toBe(false)
  })
})

describe('another tab', () => {
  it('signing out signs this tab out too', async () => {
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 + 600))
    localStorage.setItem('user', JSON.stringify(USER))
    const account = await freshService()
    await account.init()
    const other = new BroadcastChannel('cheese:session')

    other.postMessage({ type: 'signed-out' })
    await vi.waitFor(() => expect(account.loggedIn).toBe(false))

    expect(account.accessToken).toBeNull()
    other.close()
  })

  it('refreshing hands this tab the new token', async () => {
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 + 600))
    localStorage.setItem('user', JSON.stringify(USER))
    const account = await freshService()
    await account.init()
    const renewed = jwtWithExp(Date.now() / 1000 + 900)
    const other = new BroadcastChannel('cheese:session')

    other.postMessage({ type: 'token', token: renewed, user: USER })
    await vi.waitFor(() => expect(account.accessToken).toBe(renewed))

    expect(account.loggedIn).toBe(true)
    other.close()
  })
})

describe('chat requests after sign-in', () => {
  it('uses the new account and token after replacing an existing login, then clears both on logout', async () => {
    localStorage.setItem('user', JSON.stringify({ id: 9, username: 'previous-account' }))
    localStorage.setItem('cheesex.me', JSON.stringify({ id: '9', handle: 'previous-account', token: 'previous-token' }))
    const account = await freshService()
    const identity = await import('@/me')
    const api = await import('@/api')
    expect(identity.myHandle()).toBe('previous-account')

    const token = jwtWithExp(Date.now() / 1000 + 900)
    await account.login(token)
    expect(identity.myHandle()).toBe('alice')
    expect(identity.myId()).toBe('1')

    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(JSON.stringify({ code: 200, data: {} }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    try {
      await api.getTopicUnread('project', identity.myHandle())
      await api.getPrivateUnread('project', identity.myHandle())
      await api.markTopicRead('room', identity.myHandle())
      expect(fetch.mock.calls.map(([url]) => url)).toEqual([
        '/api/projects/project/topic-unread?handle=alice',
        '/api/projects/project/private-unread?handle=alice',
        '/api/topics/room/read',
      ])
      for (const [, init] of fetch.mock.calls) {
        expect(init?.headers).toMatchObject({ Authorization: `Bearer ${token}` })
      }
      expect(JSON.parse(String(fetch.mock.calls[2][1]?.body))).toEqual({ handle: 'alice' })

      await account.logout()
      expect(identity.myHandle()).toBe('')
      expect(identity.myId()).toBe('')
      expect(api.authToken()).toBe('')
      localStorage.setItem('cheesex.me', JSON.stringify({ token: 'previous-token' }))
      expect(api.authToken()).toBe('')
    } finally {
      fetch.mockRestore()
    }
  })
})
