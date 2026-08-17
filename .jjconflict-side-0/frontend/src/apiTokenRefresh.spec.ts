// 2.0 的 token 刷新。这一层是裸 `fetch`，不经过 axios 那个 401 刷新拦截器；
// 而 2.0 路由本身也不会答 401 —— 它们从 token 解析出「是谁」，解析不出就当成
// 「没有人」。两边都是静默失败，合起来就是：token 一过期，请求照发，只是变成了
// 匿名调用。左边栏冒出一堆别人的项目就是这么来的。
//
// 所以刷新必须发生在**请求之前**，不能靠对失败做出反应 —— 那个失败在传输层根本
// 看不见。下面钉的就是这个：快过期才刷、并发只刷一次、刷不动也不能把调用打断。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ensureFreshToken, refreshNow, tokenExpiresWithin } from './api'

function jwt(expMsFromNow: number): string {
  const payload = { exp: Math.floor((Date.now() + expMsFromNow) / 1000), handle: 'alice' }
  return `h.${btoa(JSON.stringify(payload))}.sig`
}

describe('tokenExpiresWithin', () => {
  it('is true for a token inside the window and false for a fresh one', () => {
    expect(tokenExpiresWithin(jwt(10_000), 60_000)).toBe(true)
    expect(tokenExpiresWithin(jwt(600_000), 60_000)).toBe(false)
  })

  it('leaves anything it cannot read alone', () => {
    // Refusing to parse must mean "don't touch it", not "refresh on every
    // call" — an opaque token would otherwise refresh once per request.
    expect(tokenExpiresWithin('not.a.jwt', 60_000)).toBe(false)
    expect(tokenExpiresWithin('', 60_000)).toBe(false)
    expect(tokenExpiresWithin(`h.${btoa(JSON.stringify({ handle: 'a' }))}.s`, 60_000)).toBe(false)
  })
})

describe('ensureFreshToken', () => {
  let calls: number

  beforeEach(() => {
    calls = 0
    localStorage.clear()
    vi.stubGlobal('fetch', async () => {
      calls += 1
      return {
        ok: true,
        json: async () => ({ code: 200, data: { accessToken: jwt(900_000) } }),
      } as unknown as Response
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('does not refresh a token that is still good', async () => {
    localStorage.setItem('accessToken', jwt(600_000))
    await ensureFreshToken()
    expect(calls).toBe(0)
  })

  it('does not refresh when there is no token at all', async () => {
    // Signed out is not expired. Refreshing here would fire a pointless
    // request on every call from a logged-out page.
    await ensureFreshToken()
    expect(calls).toBe(0)
  })

  it('replaces a token that is about to expire', async () => {
    const stale = jwt(5_000)
    localStorage.setItem('accessToken', stale)
    await ensureFreshToken()
    expect(calls).toBe(1)
    expect(localStorage.getItem('accessToken')).not.toBe(stale)
  })

  it('refreshes once for a burst of concurrent callers', async () => {
    // A page mounts and fires eight requests at once. Eight refreshes would
    // race to overwrite `accessToken` with each other's result.
    localStorage.setItem('accessToken', jwt(5_000))
    await Promise.all(Array.from({ length: 8 }, () => ensureFreshToken()))
    expect(calls).toBe(1)
  })

  it('does not throw when the refresh itself fails', async () => {
    // Offline, or the refresh cookie is gone. Sending the stale token is no
    // worse than sending nothing — but throwing here would break every caller.
    localStorage.setItem('accessToken', jwt(5_000))
    vi.stubGlobal('fetch', async () => {
      calls += 1
      throw new TypeError('network')
    })
    await expect(ensureFreshToken()).resolves.toBeUndefined()
    expect(calls).toBe(1)
  })

  it('recovers on the next call after a failed refresh', async () => {
    // The in-flight guard must be cleared in a `finally`. If a failure left it
    // set, the very first network blip would disable refresh for the session.
    localStorage.setItem('accessToken', jwt(5_000))
    vi.stubGlobal('fetch', async () => {
      calls += 1
      throw new TypeError('network')
    })
    await ensureFreshToken()
    vi.stubGlobal('fetch', async () => {
      calls += 1
      return {
        ok: true,
        json: async () => ({ code: 200, data: { accessToken: jwt(900_000) } }),
      } as unknown as Response
    })
    await ensureFreshToken()
    expect(calls).toBe(2)
  })
})

// `exp` 不是 token 唯一的死法。dev 上实测：一个签发 443 秒、还剩 457 秒的 token，
// 被两层 API 连续拒了 24 次，而几秒前刚签的那个一切正常；`decode_token` 只做纯
// JWT 校验、没有吊销表，所以只可能是签名密钥在中途变了（后端重启）。只信 `exp`
// 的后果是：用户接下来每一个请求都 401，一直到 token 快到期才会去刷新——最长 14
// 分钟。「通知铃铛必 401」就是这个形状。
describe('refreshNow：不问 exp，直接换一个', () => {
  let calls: number

  beforeEach(() => {
    calls = 0
    localStorage.clear()
    vi.stubGlobal('fetch', async () => {
      calls += 1
      return {
        ok: true,
        json: async () => ({ code: 200, data: { accessToken: jwt(900_000) } }),
      } as unknown as Response
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('哪怕 token 看起来还很新，也照刷', async () => {
    // 和 ensureFreshToken 的分工就在这一条：那个信 exp，这个不信。
    const fresh = jwt(600_000)
    localStorage.setItem('accessToken', fresh)
    await refreshNow()
    expect(calls).toBe(1)
    expect(localStorage.getItem('accessToken')).not.toBe(fresh)
  })

  it('一串并发的 401 只换一次', async () => {
    // 一个页面挂载时发八个请求，重启后八个一起 401。八次刷新会互相覆盖结果。
    localStorage.setItem('accessToken', jwt(600_000))
    await Promise.all(Array.from({ length: 8 }, () => refreshNow()))
    expect(calls).toBe(1)
  })

  it('刷新失败不抛，并且下一次还能刷', async () => {
    // in-flight 标记必须在 finally 里清掉，否则第一次网络抖动就永久关掉刷新。
    localStorage.setItem('accessToken', jwt(600_000))
    vi.stubGlobal('fetch', async () => {
      calls += 1
      throw new TypeError('network')
    })
    await expect(refreshNow()).resolves.toBeUndefined()
    vi.stubGlobal('fetch', async () => {
      calls += 1
      return {
        ok: true,
        json: async () => ({ code: 200, data: { accessToken: jwt(900_000) } }),
      } as unknown as Response
    })
    await refreshNow()
    expect(calls).toBe(2)
  })

  it('刷不出新 token 时，存的那个原样留着', async () => {
    // 换不动就别动。把 accessToken 清空会让调用方从「带着一个没人认的凭据」
    // 变成「压根没有凭据」，而后者在 2.0 那边是静默降级成匿名，更难查。
    const stale = jwt(600_000)
    localStorage.setItem('accessToken', stale)
    vi.stubGlobal('fetch', async () => {
      calls += 1
      return { ok: false, status: 401, json: async () => ({}) } as unknown as Response
    })
    await refreshNow()
    expect(localStorage.getItem('accessToken')).toBe(stale)
  })
})
