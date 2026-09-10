// Account changes must reach chat requests without reloading the page.
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

describe('current chat account', () => {
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

  it('uses the current account when an older chat identity remains', async () => {
    localStorage.setItem('cheesex.me', JSON.stringify({ id: '1', handle: 'old-account', token: 'old-token' }))
    localStorage.setItem('user', JSON.stringify({ id: 470, username: 'current-account' }))
    const mod = await import('./me')
    expect(mod.myId()).toBe('470')
    expect(mod.myHandle()).toBe('current-account')
  })

  it('follows account changes without reloading the page', async () => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'alice' }))
    const mod = await import('./me')
    expect(mod.myHandle()).toBe('alice')
    localStorage.setItem('user', JSON.stringify({ id: 2, username: 'bob' }))
    expect(mod.myHandle()).toBe('bob')
    expect(mod.myId()).toBe('2')
    localStorage.removeItem('user')
    expect(mod.myHandle()).toBe('')
    expect(mod.myId()).toBe('')
  })

  it('does not treat an obsolete chat identity as a signed-in account', async () => {
    localStorage.setItem('cheesex.me', JSON.stringify({ id: '1', handle: 'old-account', token: 'old-token' }))
    const mod = await import('./me')
    expect(mod.myHandle()).toBe('')
    expect(mod.myId()).toBe('')
  })

  it('stays empty when nobody is signed in, rather than inventing an id', async () => {
    const mod = await import('./me')
    expect(mod.myId()).toBe('')
    expect(mod.myHandle()).toBe('')
  })
})
