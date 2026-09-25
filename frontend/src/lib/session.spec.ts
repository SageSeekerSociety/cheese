// Refreshing a session that the server rotates.
//
// Every refresh replaces the refresh cookie, and the server treats the
// replaced one, presented again, as stolen: it ends the sign-in. Tabs share
// the cookie. So two tabs that refresh without knowing about each other sign
// the user out — which is what the fake server below does, exactly as the real
// one would once its grace window has passed.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Session = typeof import('./session')

// ---- The browser: one cookie jar, one lock manager, one broadcast bus ----

class Bus {
  static channels = new Set<Bus>()
  onmessage: ((event: MessageEvent) => void) | null = null
  private handlers: ((event: MessageEvent) => void)[] = []
  constructor(readonly name: string) {
    Bus.channels.add(this)
  }
  addEventListener(_type: 'message', handler: (event: MessageEvent) => void) {
    this.handlers.push(handler)
  }
  postMessage(data: unknown) {
    for (const other of Bus.channels) {
      if (other === this || other.name !== this.name) continue
      queueMicrotask(() => other.handlers.forEach((h) => h({ data } as MessageEvent)))
    }
  }
  close() {
    Bus.channels.delete(this)
  }
}

function serializingLocks() {
  let tail: Promise<unknown> = Promise.resolve()
  return {
    request<T>(_name: string, work: () => Promise<T>): Promise<T> {
      const run = tail.then(work)
      tail = run.catch(() => undefined)
      return run
    },
  }
}

// ---- The server: rotates on every refresh, ends the sign-in on reuse ----

function rotatingServer() {
  const server = { jar: 'r0', current: 'r0', previous: '', ended: false, refreshes: 0 }
  const fetch = vi.fn(async () => {
    const presented = server.jar
    // Let the other tab's request start before this one is answered.
    await new Promise((resolve) => setTimeout(resolve, 5))
    if (server.ended) return { ok: false, status: 401, json: async () => ({}) } as Response
    if (presented === server.previous) {
      server.ended = true
      return { ok: false, status: 401, json: async () => ({}) } as Response
    }
    if (presented !== server.current) return { ok: false, status: 401, json: async () => ({}) } as Response
    server.refreshes += 1
    server.previous = server.current
    server.current = `r${server.refreshes}`
    server.jar = server.current
    const token = `access-${server.refreshes}`
    return {
      ok: true,
      status: 200,
      json: async () => ({ data: { accessToken: token, user: { id: 7, username: 'alice' } } }),
    } as unknown as Response
  })
  return { server, fetch }
}

async function openTab(): Promise<Session> {
  vi.resetModules()
  return import('./session')
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('accessToken', 'access-0')
  Bus.channels.clear()
  vi.stubGlobal('BroadcastChannel', Bus)
  Object.defineProperty(navigator, 'locks', { value: serializingLocks(), configurable: true })
})

afterEach(() => {
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'locks')
})

describe('two tabs refreshing at the same moment', () => {
  it('refresh once between them, and neither is signed out', async () => {
    const { server, fetch } = rotatingServer()
    vi.stubGlobal('fetch', fetch)
    const [tabA, tabB] = [await openTab(), await openTab()]
    const signedOut = vi.fn()
    tabA.onSessionEvent((e) => e.type === 'signed-out' && signedOut())
    tabB.onSessionEvent((e) => e.type === 'signed-out' && signedOut())

    const [a, b] = await Promise.all([tabA.refreshSession(), tabB.refreshSession()])

    expect(server.ended).toBe(false)
    expect(server.refreshes).toBe(1)
    expect(a).toMatchObject({ kind: 'ok', token: 'access-1' })
    expect(b).toMatchObject({ kind: 'ok', token: 'access-1' })
    expect(signedOut).not.toHaveBeenCalled()
  })

  it('without the lock the same two refreshes end the sign-in', async () => {
    // The control: what the lock is there to prevent.
    Reflect.deleteProperty(navigator, 'locks')
    const { server, fetch } = rotatingServer()
    vi.stubGlobal('fetch', fetch)
    const [tabA, tabB] = [await openTab(), await openTab()]

    await Promise.all([tabA.refreshSession(), tabB.refreshSession()])

    expect(server.ended).toBe(true)
  })

  it('one tab refreshing tells the other the new token', async () => {
    const { fetch } = rotatingServer()
    vi.stubGlobal('fetch', fetch)
    const [tabA, tabB] = [await openTab(), await openTab()]
    const heard = vi.fn()
    tabB.onSessionEvent(heard)

    await tabA.refreshSession()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(heard).toHaveBeenCalledWith(expect.objectContaining({ type: 'token', token: 'access-1' }))
  })
})

describe('within one tab', () => {
  it('a burst of callers shares one refresh', async () => {
    const { server, fetch } = rotatingServer()
    vi.stubGlobal('fetch', fetch)
    const tab = await openTab()

    await Promise.all(Array.from({ length: 8 }, () => tab.refreshSession()))

    expect(server.refreshes).toBe(1)
    expect(server.ended).toBe(false)
    expect(localStorage.getItem('accessToken')).toBe('access-1')
  })
})

describe('what counts as signed out', () => {
  it('a 401 from the refresh signs every tab out', async () => {
    vi.stubGlobal('fetch', async () => ({ ok: false, status: 401, json: async () => ({}) }) as Response)
    const [tabA, tabB] = [await openTab(), await openTab()]
    const here = vi.fn()
    const there = vi.fn()
    tabA.onSessionEvent(here)
    tabB.onSessionEvent(there)

    await expect(tabA.refreshSession()).resolves.toEqual({ kind: 'rejected' })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(here).toHaveBeenCalledWith({ type: 'signed-out' })
    expect(there).toHaveBeenCalledWith({ type: 'signed-out' })
  })

  it.each([
    ['the network fails', async () => Promise.reject(new TypeError('network'))],
    ['the server errs', async () => ({ ok: false, status: 503, json: async () => ({}) }) as Response],
    ['another site is refused', async () => ({ ok: false, status: 403, json: async () => ({}) }) as Response],
  ])('nothing is signed out when %s', async (_why, respond) => {
    vi.stubGlobal('fetch', respond)
    const tab = await openTab()
    const heard = vi.fn()
    tab.onSessionEvent(heard)

    await expect(tab.refreshSession()).resolves.toEqual({ kind: 'unreachable' })

    expect(heard).not.toHaveBeenCalled()
    expect(localStorage.getItem('accessToken')).toBe('access-0')
  })

  it('signing out in one tab reaches the others', async () => {
    const [tabA, tabB] = [await openTab(), await openTab()]
    const there = vi.fn()
    tabB.onSessionEvent(there)

    tabA.announceSignOut()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(there).toHaveBeenCalledWith({ type: 'signed-out' })
  })
})
