// 一个页面一条房间连接。写在代码之前的规则：
//
// - 换房间不新开连接：退订上一个、订阅这一个，都在已经连着的那条上；
// - 一个房间要等服务端确认订阅才算连上，那之前它什么也收不到；确认时它知道那一刻房间
//   里最新的一条；
// - 每次订阅带的是此刻的 token，不是连接打开那一刻的；
// - 一个房间只收到它自己的帧；
// - 服务端结束一个房间（拒绝、读得太慢），别的房间不受影响；
// - 连接断了，每个房间都知道，下一次订阅重新连上；
// - 一个房间说连接已经不通（心跳没有回音），整条换掉。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ token: 't1' }))
vi.mock('@/api/http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/http')>()),
  authToken: () => auth.token,
}))

import { openRoomChannel, resetRoomLink } from './roomLink'

const links: FakeLink[] = []
class FakeLink {
  static OPEN = 1
  readyState = 0
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  sent: Record<string, unknown>[] = []
  closed = false
  constructor(readonly url: string) {
    links.push(this)
  }
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  close() {
    this.closed = true
  }
  up() {
    this.readyState = 1
    this.onopen?.()
  }
  frame(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }
}

function watch(topic: string) {
  const room = openRoomChannel(topic)
  const seen: Record<string, unknown>[] = []
  const events: string[] = []
  room.onopen = () => events.push('open')
  room.onclose = () => events.push('close')
  room.onmessage = (event) => seen.push(JSON.parse(event.data))
  return { room, seen, events }
}

beforeEach(() => {
  links.length = 0
  auth.token = 't1'
  vi.stubGlobal('WebSocket', FakeLink)
  resetRoomLink()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('一个页面一条房间连接', () => {
  it('换房间不新开连接', async () => {
    const a = watch('room-a')
    expect(links).toHaveLength(1)
    links[0].up()
    links[0].frame({ type: 'subscribed', topic: 'room-a' })
    expect(a.events).toEqual(['open'])

    a.room.close()
    const b = watch('room-b')
    links[0].frame({ type: 'subscribed', topic: 'room-b' })

    expect(links).toHaveLength(1)
    expect(links[0].sent.map((f) => [f.type, f.topic])).toEqual([
      ['subscribe', 'room-a'],
      ['unsubscribe', 'room-a'],
      ['subscribe', 'room-b'],
    ])
    expect(b.events).toEqual(['open'])
  })

  it('服务端确认之前，房间不算连上', async () => {
    const a = watch('room-a')
    links[0].up()
    expect(a.events).toEqual([])
    a.room.send(JSON.stringify({ type: 'typing' }))
    expect(links[0].sent.filter((f) => f.type === 'typing')).toEqual([])
  })

  it('确认订阅时，房间知道那一刻最后存进来的是几号', async () => {
    const a = watch('room-a')
    let newestOnOpen: number | null | undefined
    a.room.onopen = () => (newestOnOpen = a.room.newest)
    links[0].up()
    links[0].frame({ type: 'subscribed', topic: 'room-a', newest: 9 })
    expect(newestOnOpen).toBe(9)
  })

  it('每次订阅带的是此刻的 token', async () => {
    watch('room-a')
    links[0].up()
    auth.token = 't2'
    watch('room-b')
    expect(links[0].sent.map((f) => [f.topic, f.token])).toEqual([
      ['room-a', 't1'],
      ['room-b', 't2'],
    ])
  })

  it('一个房间只收到它自己的帧，说的话也只进它自己', async () => {
    const a = watch('room-a')
    const b = watch('room-b')
    links[0].up()
    links[0].frame({ type: 'subscribed', topic: 'room-a' })
    links[0].frame({ type: 'subscribed', topic: 'room-b' })

    links[0].frame({ type: 'user_block', topic: 'room-b', block: { id: 'x' } })
    expect(a.seen).toEqual([])
    expect(b.seen).toEqual([{ type: 'user_block', block: { id: 'x' } }])

    a.room.send(JSON.stringify({ type: 'ping' }))
    expect(links[0].sent.at(-1)).toEqual({ type: 'ping', topic: 'room-a' })
  })

  it('服务端结束一个房间，别的房间照常', async () => {
    const a = watch('room-a')
    const b = watch('room-b')
    links[0].up()
    links[0].frame({ type: 'subscribed', topic: 'room-a' })
    links[0].frame({ type: 'subscribed', topic: 'room-b' })

    links[0].frame({ type: 'error', topic: 'room-a', code: 'forbidden' })
    links[0].frame({ type: 'closed', topic: 'room-a' })
    links[0].frame({ type: 'user_block', topic: 'room-b', block: { id: 'y' } })

    expect(a.seen).toEqual([{ type: 'error', code: 'forbidden' }])
    expect(a.events).toEqual(['open', 'close'])
    expect(b.events).toEqual(['open'])
    expect(b.seen).toHaveLength(1)
    expect(links[0].closed).toBe(false)
  })

  it('连接断了，每个房间都知道；下一次订阅重新连上', async () => {
    const a = watch('room-a')
    const b = watch('room-b')
    links[0].up()
    links[0].onclose?.()
    expect(a.events).toEqual(['close'])
    expect(b.events).toEqual(['close'])

    watch('room-a')
    expect(links).toHaveLength(2)
    links[1].up()
    expect(links[1].sent).toEqual([{ type: 'subscribe', topic: 'room-a', token: 't1' }])
  })

  it('一个房间说连接已经不通，整条换掉', async () => {
    const a = watch('room-a')
    const b = watch('room-b')
    links[0].up()
    links[0].frame({ type: 'subscribed', topic: 'room-a' })
    links[0].frame({ type: 'subscribed', topic: 'room-b' })

    a.room.onclose = null
    a.room.drop()

    expect(links[0].closed).toBe(true)
    expect(b.events).toEqual(['open', 'close'])
    watch('room-b')
    expect(links).toHaveLength(2)
  })
})
