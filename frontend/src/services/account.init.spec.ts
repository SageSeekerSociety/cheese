// 启动时的会话恢复：过期的访问令牌必须先续签，再宣布登录。
//
// 线上现象：每次进页面 console 都有一条 `GET /api/notifications/unread-count
// → 401`，铃铛未读数永远是 0。后端那条路由是有的（contract 测试里带合法令牌是
// 200），401 是货真价实的凭证被拒——访问令牌只活 15 分钟，而 init() 以前不看
// exp，直接把 loggedIn 置 true，铃铛就顶着一个死令牌开打。
//
// 页面上别的请求（topics/blocks/projects）在这个部署里不鉴权，所以只有铃铛会
// 暴露出来。这份测试盯的就是「loggedIn 翻 true 时，手里是不是一个活令牌」。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const refreshAccessToken = vi.fn()
const getCurrentUser = vi.fn()

vi.mock('@/network/api/users', () => ({
  UserApi: {
    refreshAccessToken: (...args: unknown[]) => refreshAccessToken(...args),
    getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  },
}))

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

/**
 * 和被测代码同一份模块图里的 BusinessError。
 *
 * `vi.resetModules()` 之后 account.ts 会重新 import 一次错误类型，拿到的是一个
 * 新的类对象；spec 顶部静态 import 的那份和它 **不是同一个类**，`instanceof`
 * 判不出来（第一版就栽在这儿）。所以要在 reset 之后现取。
 */
async function freshBusinessError() {
  const mod = await import('@/network/types/error')
  return mod.BusinessError
}

beforeEach(() => {
  localStorage.clear()
  refreshAccessToken.mockReset()
  getCurrentUser.mockReset()
  getCurrentUser.mockResolvedValue({ data: { user: USER } })
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

    expect(refreshAccessToken).not.toHaveBeenCalled()
    expect(account.loggedIn).toBe(true)
  })

  it('令牌已过期：先续签，拿到新令牌后才 loggedIn=true', async () => {
    const stale = jwtWithExp(Date.now() / 1000 - 60)
    const fresh = jwtWithExp(Date.now() / 1000 + 900)
    localStorage.setItem('accessToken', stale)
    localStorage.setItem('user', JSON.stringify(USER))
    refreshAccessToken.mockResolvedValue({ data: { accessToken: fresh, user: USER } })

    const account = await freshService()
    await account.init()

    expect(refreshAccessToken).toHaveBeenCalledTimes(1)
    expect(account.loggedIn).toBe(true)
    // 关键：loggedIn 翻 true 的那一刻，手里必须是新令牌，不能还是那个死的。
    expect(account.accessToken).toBe(fresh)
    expect(localStorage.getItem('accessToken')).toBe(fresh)
  })

  it('续签被服务端拒了：登出，而不是顶着假登录态一路撒 401', async () => {
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 - 60))
    localStorage.setItem('user', JSON.stringify(USER))
    const account = await freshService()
    const BusinessError = await freshBusinessError()
    refreshAccessToken.mockRejectedValue(new BusinessError('Refresh token is missing', 401))

    await account.init()

    expect(account.loggedIn).toBe(false)
    expect(account.accessToken).toBeNull()
    expect(localStorage.getItem('accessToken')).toBeNull()
  })

  it('续签是网络错误：不登录，但保留本地会话，下次进页面再续', async () => {
    const stale = jwtWithExp(Date.now() / 1000 - 60)
    localStorage.setItem('accessToken', stale)
    localStorage.setItem('user', JSON.stringify(USER))
    // 拦截器对「没拿到响应」的情况抛的是裸 Error，不是 BusinessError。
    refreshAccessToken.mockRejectedValue(new Error('网络请求失败'))

    const account = await freshService()
    await account.init()

    // 没有活令牌就不宣布登录——否则还是会撒 401。
    expect(account.loggedIn).toBe(false)
    // 但刷新令牌还有 30 天，别拿网络抖动当登出理由。
    expect(localStorage.getItem('accessToken')).toBe(stale)
    expect(localStorage.getItem('user')).not.toBeNull()
  })

  it('续签返回体缺 accessToken/user 也算失败', async () => {
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 - 60))
    localStorage.setItem('user', JSON.stringify(USER))
    refreshAccessToken.mockResolvedValue({ data: { accessToken: '', user: null } })

    const account = await freshService()
    await account.init()

    expect(account.loggedIn).toBe(false)
  })

  it('本来就没登录过：什么都不做，也不去续签', async () => {
    const account = await freshService()
    await account.init()

    expect(refreshAccessToken).not.toHaveBeenCalled()
    expect(account.loggedIn).toBe(false)
  })
})
