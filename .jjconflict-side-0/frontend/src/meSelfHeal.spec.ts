// `me` is a module-scope ref initialised ONCE, at import time, from
// localStorage. Sign-in is a `router.replace` (SignIn.vue) — an SPA navigation,
// never a reload — so after logging in, that snapshot is still the logged-OUT
// one until something re-reads storage. `myHandle()` has always re-read; the
// numeric id had no such accessor, and reading `me.value?.id` raw made the
// 连接 GitHub 账号 section render its 「未登录」 error branch to a user who had
// just logged in.
import { beforeEach, describe, expect, it, vi } from 'vitest'

function storage(): Storage {
  const map = new Map<string, string>()
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    get length() {
      return map.size
    },
  }
}

describe('myId', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.stubGlobal('localStorage', storage())
  })

  it('re-reads storage when the boot snapshot predates the login', async () => {
    // Import FIRST, with nothing stored — this is the logged-out boot.
    const mod = await import('./me')
    expect(mod.myId()).toBe('')

    // Now the user signs in: AccountService writes `user`, and the SPA never
    // reloads, so nothing re-runs the module initialiser.
    localStorage.setItem('user', JSON.stringify({ id: 467, username: 'xiaoyuer', nickname: '小鱼儿' }))

    expect(mod.myId()).toBe('467')
  })

  it('prefers the cheesex.me mirror when main.ts has written one', async () => {
    localStorage.setItem('cheesex.me', JSON.stringify({ id: '470', handle: 'wangchangxin', name: 'w' }))
    const mod = await import('./me')
    expect(mod.myId()).toBe('470')
  })

  it('stays empty when nobody is signed in, rather than inventing an id', async () => {
    const mod = await import('./me')
    expect(mod.myId()).toBe('')
    expect(mod.myHandle()).toBe('')
  })
})
