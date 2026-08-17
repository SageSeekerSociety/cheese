// 401 要当场换凭据再试一次，而不是等 `exp` 到点。
//
// dev 上实测出来的形状：一个签发 443 秒、还剩 457 秒的 token，被 1.0 和 2.0 两层
// API 连续拒了 24 次；同一时刻新签的那个一切正常。后端的 `decode_token` 只做纯
// JWT 校验、没有任何吊销表，所以只可能是签名密钥在两次之间变了（后端重启）。
//
// 在这之前，前端只在「快到期」时刷新，于是这种情况下用户接下来的**每一个请求**
// 都 401，最长要熬 14 分钟才等到那次刷新。页面上的样子就是通知铃铛一直红着报错、
// 侧栏空着——控制台里那一串每 30 秒一次的 401 就是它。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { listProjects } from './api'

function jwt(tag: string): string {
  const payload = { exp: Math.floor((Date.now() + 600_000) / 1000), handle: 'alice', tag }
  return `h.${btoa(JSON.stringify(payload))}.sig`
}

const OK = {
  ok: true,
  status: 200,
  json: async () => ({ code: 200, data: { data: [{ id: 'p1' }], total: 1 } }),
} as unknown as Response

const UNAUTHORIZED = {
  ok: false,
  status: 401,
  json: async () => ({ code: 401, message: 'nope' }),
} as unknown as Response

describe('一个 401 之后，换凭据重试一次', () => {
  let seen: string[]

  beforeEach(() => {
    seen = []
    localStorage.clear()
    localStorage.setItem('accessToken', jwt('old'))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('换到新 token 后重试，调用方看到的是成功', async () => {
    // 这条就是线上那个故障：token 没过期，但服务端不认了。
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const u = String(url)
      if (u.includes('refresh-token')) {
        localStorage.setItem('accessToken', jwt('new'))
        return { ok: true, json: async () => ({ data: { accessToken: jwt('new') } }) } as unknown as Response
      }
      const auth = (init?.headers as Record<string, string>)?.Authorization ?? ''
      const tag = JSON.parse(atob(auth.replace('Bearer ', '').split('.')[1] ?? 'e30=')).tag
      seen.push(tag)
      return seen.length === 1 ? UNAUTHORIZED : OK
    })

    const out = await listProjects()
    expect(out.total).toBe(1)
    // 第一次带旧的、第二次带新的 —— 重试必须真的用上换来的那个。
    expect(seen).toEqual(['old', 'new'])
  })

  it('换不到新 token 就不重试，401 照常抛出去', async () => {
    // 服务端为别的理由 401（比如这个人真没权限）时，无限重试只会把它变成一个
    // 打不完的循环，而调用方永远等不到答案。
    vi.stubGlobal('fetch', async (url: string) => {
      if (String(url).includes('refresh-token')) {
        return { ok: false, status: 401, json: async () => ({}) } as unknown as Response
      }
      seen.push('call')
      return UNAUTHORIZED
    })

    await expect(listProjects()).rejects.toMatchObject({ status: 401 })
    expect(seen).toEqual(['call'])
  })

  it('只重试一次，不会没完没了', async () => {
    // 每次刷新都给出一个新 token，但服务端照拒。没有这个上限，它会一直转。
    let n = 0
    vi.stubGlobal('fetch', async (url: string) => {
      if (String(url).includes('refresh-token')) {
        n += 1
        localStorage.setItem('accessToken', jwt(`n${n}`))
        return { ok: true, json: async () => ({ data: { accessToken: jwt(`n${n}`) } }) } as unknown as Response
      }
      seen.push('call')
      return UNAUTHORIZED
    })

    await expect(listProjects()).rejects.toMatchObject({ status: 401 })
    expect(seen).toEqual(['call', 'call'])
  })

  it('401 的重试不吃掉 502 的重试额度', async () => {
    // 两者是两套预算：一次 401 之后紧跟一次网关抖动，仍然应该被 GET 的退避重试
    // 接住。把 401 记成一次 attempt 的话，这里就会少一次机会。
    let refreshed = false
    const script = [UNAUTHORIZED, { ok: false, status: 502, json: async () => ({}) } as unknown as Response, OK]
    vi.stubGlobal('fetch', async (url: string) => {
      if (String(url).includes('refresh-token')) {
        refreshed = true
        localStorage.setItem('accessToken', jwt('new'))
        return { ok: true, json: async () => ({ data: { accessToken: jwt('new') } }) } as unknown as Response
      }
      return script[seen.push('call') - 1] ?? OK
    })

    const out = await listProjects()
    expect(refreshed).toBe(true)
    expect(out.total).toBe(1)
    expect(seen).toEqual(['call', 'call', 'call'])
  })
})
