// 跨着一次「改坏旧接口」的发版开着的一页：后端说它落后了（409 ClientOutdated，或房间连接
// 以 4409 关掉），它就请人刷新——不替人刷新，因为人写了一半的东西不全在磁盘上；人点了
// 「刷新」，先让新版的 worker 接管再刷新。
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('virtual:pwa-register', () => ({ registerSW: () => async () => {} }))

type Notice = { text: string; action?: { label?: string; onClick?: () => void } }
const notices: Notice[] = []
vi.mock('vuetify-sonner', () => ({
  toast: {
    warning: (text: string, options?: Omit<Notice, 'text'>) => {
      notices.push({ text, ...options })
      return notices.length
    },
    dismiss: () => {},
  },
}))

const GENERATION = import.meta.env.VITE_API_GENERATION

let order: string[]

/** A browser whose server already has the next build, and its worker waiting. */
function fakeBrowser() {
  const container = new EventTarget()
  const registration = {
    waiting: {
      postMessage() {
        order.push('worker takes over')
        queueMicrotask(() => container.dispatchEvent(new Event('controllerchange')))
      },
    },
    update: async () => {},
  }
  Object.assign(container, { getRegistration: async () => registration })
  vi.stubGlobal('navigator', { ...navigator, language: navigator.language, serviceWorker: container })
}

function outdated(): Response {
  return new Response(
    JSON.stringify({
      code: 409,
      message: '这个页面的版本已过期，刷新后再继续',
      error: { name: 'ClientOutdated', message: '这个页面的版本已过期，刷新后再继续', retryable: false, data: null },
    }),
    { status: 409, headers: { 'Content-Type': 'application/json' } }
  )
}

function ok(): Response {
  return new Response(JSON.stringify({ code: 200, message: 'ok', data: 'fine' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

let reload: ReturnType<typeof vi.fn>
let fetchMock: ReturnType<typeof vi.fn>
let answer: () => Response

beforeEach(() => {
  vi.resetModules()
  notices.length = 0
  order = []
  reload = vi.fn(() => order.push('reload'))
  vi.stubGlobal('location', { ...window.location, reload })
  answer = outdated
  fetchMock = vi.fn(async () => answer())
  vi.stubGlobal('fetch', fetchMock)
  fakeBrowser()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** The page's first screen is drawn (main.ts arms this on the app's router). */
async function paintFirstScreen() {
  const { armAfterFirstPaint, firstPaintDone } = await import('./lib/afterFirstPaint')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: {} }] })
  armAfterFirstPaint(router)
  await router.push('/')
  await vi.waitFor(() => expect(firstPaintDone.value).toBe(true))
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('a page the backend calls outdated', () => {
  it('says which API generation it was built for on every request', async () => {
    answer = ok
    const { request } = await import('./api')
    await request('/projects')

    expect(GENERATION).toMatch(/^\d+$/)
    expect(new Headers((fetchMock.mock.calls[0] as [string, RequestInit])[1].headers).get('X-Cheese-Api-Gen')).toBe(
      GENERATION
    )
  })

  it('asks once for a refresh, however many requests are refused, and does not reload by itself', async () => {
    const { request, ApiError } = await import('./api')
    await paintFirstScreen()

    const refused = await Promise.allSettled([request('/projects'), request('/topics', { method: 'POST' })])
    await tick()

    expect(refused.map((r) => r.status === 'rejected' && r.reason instanceof ApiError && r.reason.code)).toEqual([
      'ClientOutdated',
      'ClientOutdated',
    ])
    expect(notices).toHaveLength(1)
    expect(reload).not.toHaveBeenCalled()
  })

  it('reloads onto the new build when told to, after its worker takes over', async () => {
    const { request } = await import('./api')
    await paintFirstScreen()
    await request('/projects').catch(() => {})

    notices[0].action?.onClick?.()

    await vi.waitFor(() => expect(order).toEqual(['worker takes over', 'reload']))
  })

  it('keeps the request for a refresh until there is a screen to show it on', async () => {
    const { request } = await import('./api')
    // Refused on its first requests, before anything is on screen to hold a notice.
    await request('/projects').catch(() => {})
    await tick()
    expect(notices).toEqual([])

    await paintFirstScreen()

    expect(notices).toHaveLength(1)
  })

  it('asks for a refresh when its room connection is closed as outdated', async () => {
    const sockets: FakeSocket[] = []
    class FakeSocket {
      onopen: (() => void) | null = null
      onmessage: ((e: MessageEvent) => void) | null = null
      onerror: (() => void) | null = null
      onclose: ((e: CloseEvent) => void) | null = null
      constructor(readonly url: string) {
        sockets.push(this)
      }
      send() {}
      close() {}
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    await paintFirstScreen()
    const { openRoomChannel } = await import('./lib/roomLink')

    openRoomChannel('room-1')
    expect(new URL(sockets[0].url).searchParams.get('api_gen')).toBe(GENERATION)
    // happy-dom's CloseEvent drops `code`; a browser's carries it.
    sockets[0].onclose?.({ code: 4409 } as CloseEvent)

    expect(notices).toHaveLength(1)
    expect(reload).not.toHaveBeenCalled()
  })
})
