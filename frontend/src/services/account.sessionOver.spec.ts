// 服务端把一次**正在用**的登录结束了（续签被它答 401）之后：人要被告知，并且被送到
// 能重新登录的地方。
//
// 线上现象（手机应用）：改动、文件、概览几个面板同时挂着一句「登录状态无效或已过期，
// 请重新登录」，走哪都有，一直不消。那一句是服务端对那张访问令牌的答复——它已经不认
// 了。前端本该自动换一张，那次续签正是被服务端自己拒掉的，而拒掉之后没人结束这次
// 登录：本地会话（令牌、用户、各个缓存）原样留着，于是每个面板继续拿着那张没人认的
// 令牌去撞同一面墙。
//
// 这一份盯的就是那一步：拒掉之后要把会话放下、把人送到登录页；而人自己退出登录（别的
// 标签页里）不是这一回事，冷打开时发现会话早就没了也不是——那时候没有必要把人从某个
// 页面上拽走。
import { toast } from 'vuetify-sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getCurrentUser = vi.fn()
const replace = vi.fn()

vi.mock('@/api/users', () => ({
  UserApi: {
    getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
    setTimezone: () => Promise.resolve({}),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

// `sessionOver()` 动态取路由（见那里的说明）——这里换成记账用的替身。
vi.mock('@/router', () => ({
  default: {
    currentRoute: { value: { name: 'task' } },
    replace: (...args: unknown[]) => replace(...args),
  },
}))

// 标签页之间的那条通道：一个测试一份，免得一条消息漏进下一个测试。
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

const refresh = vi.fn()

function answer(status: number, body: unknown = {}): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as unknown as Response
}

function jwtWithExp(expSeconds: number): string {
  const payload = btoa(JSON.stringify({ sub: '1', type: 'access', exp: expSeconds }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
  return `header.${payload}.signature`
}

const USER = { id: 1, username: 'alice', nickname: '爱丽丝' }

/** 把这件事做完：续签的动作是异步的，`forget()` 也是。 */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

/** 一个正登录着、手里握着活令牌的标签页。 */
async function signedIn() {
  localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 + 900))
  localStorage.setItem('user', JSON.stringify(USER))
  const { default: account } = await import('./account')
  await account.init()
  return account
}

beforeEach(() => {
  localStorage.clear()
  refresh.mockReset()
  getCurrentUser.mockReset()
  getCurrentUser.mockResolvedValue({ user: USER })
  replace.mockReset()
  vi.mocked(toast.error).mockClear()
  tabs = new Set()
  vi.resetModules()
  vi.stubGlobal('BroadcastChannel', FakeChannel)
  vi.stubGlobal('fetch', async (url: string) => {
    if (String(url).includes('refresh-token')) return refresh()
    throw new Error(`unexpected request ${url}`)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('服务端把正在用的登录结束了', () => {
  it('告诉人，放下会话，送到登录页', async () => {
    const account = await signedIn()
    const session = await import('@/lib/session')
    refresh.mockResolvedValue(answer(401))

    await session.refreshSession()
    await settle()

    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(replace).toHaveBeenCalledWith({ name: 'SignIn' })
    expect(account.loggedIn).toBe(false)
    expect(account.accessToken).toBeNull()
    expect(localStorage.getItem('accessToken')).toBeNull()
  })

  it('会话真的放下了才跳 —— 不然登录页的守卫会把这一跳弹回来', async () => {
    // `leaveSignInWhenSignedIn`：`loggedIn` 还是 true 的时候导航到登录页，它会把你
    // 送回原来的页面。人就看着红了一下又回到原地。
    const account = await signedIn()
    const session = await import('@/lib/session')
    const atJump: { loggedIn: boolean; token: string | null }[] = []
    replace.mockImplementation(() => {
      atJump.push({ loggedIn: account.loggedIn, token: localStorage.getItem('accessToken') })
    })
    refresh.mockResolvedValue(answer(401))

    await session.refreshSession()
    await settle()

    expect(atJump).toEqual([{ loggedIn: false, token: null }])
  })

  it('一次会话结束只提示一次，不随着每个面板的 401 重复', async () => {
    const session = await import('@/lib/session')
    await signedIn()
    refresh.mockResolvedValue(answer(401))

    // 一个页面挂载时几个面板各自 401，各自来换一次。
    await Promise.all(Array.from({ length: 3 }, () => session.refreshSession()))
    await settle()

    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(replace).toHaveBeenCalledTimes(1)
  })

  it('冷打开时续签就被拒：会话清掉，但不提示、不跳', async () => {
    // 隔了一个月再打开：刷新 cookie 已经过期，人本来就还没进来，没有「从哪个页面
    // 上拽走」这回事。
    localStorage.setItem('accessToken', jwtWithExp(Date.now() / 1000 - 60))
    localStorage.setItem('user', JSON.stringify(USER))
    const { default: account } = await import('./account')
    refresh.mockResolvedValue(answer(401))

    await account.init()
    await settle()

    expect(account.loggedIn).toBe(false)
    expect(localStorage.getItem('accessToken')).toBeNull()
    expect(toast.error).not.toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })
})
