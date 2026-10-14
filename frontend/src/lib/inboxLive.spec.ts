/** 这个浏览器和服务端之间那条「收件箱多了一行」的连接（`inboxLive.ts`）。
 *
 *  从页面这一侧看它要做到的：凭证随第一条消息交出去（浏览器的 WebSocket 带不了
 *  请求头，放进地址会被访问日志记下）；连上时那一帧只是「现在到几号了」，不算新的；
 *  断了会再连，而且每次换一张新凭证；重连等的时间抖开、有上限；退出登录就断开、不再连。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { onInboxChange, reconnectDelay, startInboxLive } from './inboxLive'

class FakeSocket {
  static opened: FakeSocket[] = []
  sent: string[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) {
    FakeSocket.opened.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  close() {
    if (this.closed) return
    this.closed = true
    this.onclose?.()
  }
  // 服务端那一侧的动作
  open() {
    this.onopen?.()
  }
  frame(latest: number | null) {
    this.onmessage?.({ data: JSON.stringify({ kind: 'inbox', latest }) })
  }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  FakeSocket.opened = []
  vi.stubGlobal('WebSocket', FakeSocket)
  // 每页各连一条的那条路：选哪一页去连另有浏览器自己的 Web Locks 管。
  vi.stubGlobal('BroadcastChannel', undefined)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('收件箱的实时连接', () => {
  it('凭证随第一条消息交出去，不放进地址', async () => {
    const stop = startInboxLive(async () => 'credential-1')
    await flush()
    const socket = FakeSocket.opened[0]
    socket.open()

    expect(socket.url).toMatch(/\/api\/notifications\/inbox$/)
    expect(socket.url).not.toContain('credential-1')
    expect(JSON.parse(socket.sent[0])).toEqual({ token: 'credential-1' })
    stop()
  })

  it('连上时那一帧不算新的，之后每一帧都是', async () => {
    const heard = vi.fn()
    const unsubscribe = onInboxChange(heard)
    const stop = startInboxLive(async () => 'credential')
    await flush()
    const socket = FakeSocket.opened[0]
    socket.open()

    socket.frame(10)
    expect(heard).not.toHaveBeenCalled()
    socket.frame(11)
    expect(heard).toHaveBeenCalledTimes(1)

    stop()
    unsubscribe()
  })

  it('断了就换一张新凭证再连；退出登录后不再连', async () => {
    vi.useFakeTimers()
    let issued = 0
    const stop = startInboxLive(async () => `credential-${++issued}`)
    await vi.advanceTimersByTimeAsync(0)
    FakeSocket.opened[0].open()
    FakeSocket.opened[0].frame(1)

    FakeSocket.opened[0].close() // 服务端那边断了（部署、网络）
    await vi.advanceTimersByTimeAsync(MAX_WAIT_MS)
    expect(FakeSocket.opened).toHaveLength(2)
    FakeSocket.opened[1].open()
    expect(JSON.parse(FakeSocket.opened[1].sent[0])).toEqual({ token: 'credential-2' })

    stop()
    expect(FakeSocket.opened[1].closed).toBe(true)
    await vi.advanceTimersByTimeAsync(MAX_WAIT_MS * 4)
    expect(FakeSocket.opened).toHaveLength(2)
  })

  it('重连等的时间在 0 和上限之间抖开，越失败越长，最长 30 秒', () => {
    expect(reconnectDelay(0, () => 0)).toBe(0)
    expect(reconnectDelay(1, () => 0.999)).toBeLessThan(reconnectDelay(3, () => 0.999))
    expect(reconnectDelay(20, () => 0.999)).toBeLessThanOrEqual(MAX_WAIT_MS)
  })
})

const MAX_WAIT_MS = 30_000
