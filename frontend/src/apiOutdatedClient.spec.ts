// 跨着一次「改坏旧接口」的发版开着的一页：后端说它落后了（409 ClientOutdated，或房间连接
// 以 4409 关掉），它就换上新版——先让新版的 worker 接管再刷新，只刷一次；手里还有没发出去的
// 消息时，不替人刷新，留一个「刷新」让人自己点。
import { effectScope } from 'vue'
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
  sessionStorage.clear()
  localStorage.clear()
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

/** A page freshly loaded: its modules start from nothing, the tab's session storage stays. */
async function loadPage() {
  vi.resetModules()
  return await import('./api')
}

async function settled(work: Promise<unknown>) {
  await work.catch(() => {})
  await vi.waitFor(() => expect(order.length + notices.length).toBeGreaterThan(0))
  // Let the worker hand-over and the reload behind it run out.
  await new Promise((resolve) => setTimeout(resolve, 0))
}

describe('a page the backend calls outdated', () => {
  it('says which API generation it was built for on every request', async () => {
    answer = ok
    const { request } = await loadPage()
    await request('/projects')

    expect(GENERATION).toMatch(/^\d+$/)
    expect(new Headers((fetchMock.mock.calls[0] as [string, RequestInit])[1].headers).get('X-Cheese-Api-Gen')).toBe(
      GENERATION
    )
  })

  it('loads the new build once, after its worker takes over, however many requests are refused', async () => {
    const { request, ApiError } = await loadPage()

    const refused = await Promise.allSettled([request('/projects'), request('/topics', { method: 'POST' })])
    await settled(Promise.resolve())

    expect(refused.map((r) => r.status === 'rejected' && r.reason instanceof ApiError && r.reason.code)).toEqual([
      'ClientOutdated',
      'ClientOutdated',
    ])
    expect(order).toEqual(['worker takes over', 'reload'])
    expect(notices).toEqual([])
  })

  it('asks instead when the page holds a message not yet sent, and reloads when told to', async () => {
    const { request } = await loadPage()
    const { useOutbox } = await import('./components/room/composables/useOutbox')
    const scope = effectScope()
    const outbox = scope.run(() => useOutbox({ send: () => new Promise(() => {}), onDelivered: () => {} }))!
    outbox.enqueue({ content: '还在路上的一句话' })

    await settled(request('/projects'))

    expect(reload).not.toHaveBeenCalled()
    expect(notices).toHaveLength(1)
    notices[0].action?.onClick?.()
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce())
    scope.stop()
  })

  it('asks instead of reloading again when the build it reloaded onto is still outdated', async () => {
    await settled((await loadPage()).request('/projects'))
    expect(reload).toHaveBeenCalledOnce()

    await settled((await loadPage()).request('/projects'))

    expect(reload).toHaveBeenCalledOnce()
    expect(notices).toHaveLength(1)
  })

  it('reloads when its room connection is closed as outdated', async () => {
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
    await loadPage()
    const { openRoomChannel } = await import('./lib/roomLink')

    openRoomChannel('room-1')
    expect(new URL(sockets[0].url).searchParams.get('api_gen')).toBe(GENERATION)
    // happy-dom's CloseEvent drops `code`; a browser's carries it.
    sockets[0].onclose?.({ code: 4409 } as CloseEvent)
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce())
  })
})
