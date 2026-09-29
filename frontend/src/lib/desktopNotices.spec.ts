import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The server's side: what a push would have said after a given point, and what waits.
const feed = vi.hoisted(() => ({ items: [] as { id: number; title: string; body: string; url: string }[] }))
const waiting = vi.hoisted(() => ({ count: 0 }))
vi.mock('@/network/api/notifications', () => ({
  NotificationsApi: {
    pushFeed: vi.fn(async (after: number | null) => {
      const latest = feed.items.length ? Math.max(...feed.items.map((i) => i.id)) : null
      const items = after === null ? [] : feed.items.filter((i) => i.id > after)
      return { data: { latest, items } }
    }),
  },
}))
vi.mock('@/api', () => ({
  listAwaitingMe: vi.fn(async () => ({ data: Array.from({ length: waiting.count }) })),
}))

import { DESKTOP_NOTICE_EVERY_MS, watchForDesktopNotices } from './desktopNotices'

type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }

function desktopApp(can: string[]) {
  const invoke = vi.fn<(cmd: string, args?: Record<string, unknown>) => Promise<undefined>>(async () => undefined)
  ;(window as AppWindow).__TAURI__ = { core: { invoke } }
  ;(window as AppWindow).__CHEESE_APP__ = { origin: 'https://okcheese.com', can }
  return invoke
}

const shown = (invoke: ReturnType<typeof desktopApp>) =>
  invoke.mock.calls.filter(([cmd]) => cmd === 'notify').map(([, args]) => (args as { title: string }).title)

async function nextLook() {
  await vi.advanceTimersByTimeAsync(DESKTOP_NOTICE_EVERY_MS)
}

const notice = (id: number, title: string) => ({ id, title, body: '', url: `/projects/p/topics/${id}` })

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  feed.items = []
  waiting.count = 0
})

afterEach(() => {
  vi.useRealTimers()
  delete (window as AppWindow).__TAURI__
  delete (window as AppWindow).__CHEESE_APP__
})

describe('watchForDesktopNotices', () => {
  it('shows what arrives after the app started, once each', async () => {
    feed.items = [notice(1, '装 app 之前的事')]
    const invoke = desktopApp(['notify', 'badge'])
    const stop = watchForDesktopNotices(7)
    await vi.advanceTimersByTimeAsync(0)

    feed.items.push(notice(2, '改动已就绪，待你审阅'))
    await nextLook()
    await nextLook()

    expect(shown(invoke)).toEqual(['改动已就绪，待你审阅'])
    stop()
  })

  it('shows the first notice of an account that has never had one', async () => {
    const invoke = desktopApp(['notify'])
    const stop = watchForDesktopNotices(7)
    await vi.advanceTimersByTimeAsync(0)

    feed.items.push(notice(1, '用哪个数据库？'))
    await nextLook()

    expect(shown(invoke)).toEqual(['用哪个数据库？'])
    stop()
  })

  it('does not show again what was shown before the app restarted', async () => {
    const first = desktopApp(['notify'])
    let stop = watchForDesktopNotices(7)
    await vi.advanceTimersByTimeAsync(0)
    feed.items.push(notice(1, '改动已就绪，待你审阅'))
    await nextLook()
    stop()
    expect(shown(first)).toHaveLength(1)

    const again = desktopApp(['notify'])
    stop = watchForDesktopNotices(7)
    await vi.advanceTimersByTimeAsync(0)
    await nextLook()
    expect(shown(again)).toEqual([])
    stop()
  })

  it('puts the count of waiting things on the icon, and clears it on sign-out', async () => {
    waiting.count = 3
    const invoke = desktopApp(['badge'])
    const stop = watchForDesktopNotices(7)
    await vi.advanceTimersByTimeAsync(0)
    expect(invoke).toHaveBeenLastCalledWith('set_badge', { count: 3 })

    stop()
    expect(invoke).toHaveBeenLastCalledWith('set_badge', { count: 0 })
  })

  it('asks nothing of an app that cannot show notifications', async () => {
    feed.items = [notice(1, '旧的')]
    const invoke = desktopApp([])
    const stop = watchForDesktopNotices(7)
    feed.items.push(notice(2, '新的'))
    await nextLook()
    stop()
    expect(invoke).not.toHaveBeenCalled()
  })
})
