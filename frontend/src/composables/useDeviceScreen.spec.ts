// 现场 socket 的那几件事：什么时候算连上、回来的帧怎么分、换一台屏幕怎么办、断线
// 之后什么时候重连。画面本身在 xterm 那一半，这里钉的是它拿到的东西。
import type { Ref } from 'vue'
import type { DeviceScreenStatus } from './useDeviceScreen'

import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useDeviceScreen } from './useDeviceScreen'

vi.mock('@/api', () => ({
  screenWsUrl: (sid: string) => `ws://screen.test/connector/session/${sid}/screen`,
}))

/** 一个能由测试摆弄的 socket：开着、来一帧、掉线，都不必真有服务器。 */
class FakeSocket {
  static OPEN = 1
  static instances: FakeSocket[] = []
  static get last(): FakeSocket {
    return FakeSocket.instances[FakeSocket.instances.length - 1]
  }

  url: string
  binaryType = ''
  readyState = 0
  sent: (string | Uint8Array)[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }

  send(data: string | Uint8Array) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3
  }

  /** 连上了：后端把这一台会话挂上的那一刻。 */
  connect() {
    this.readyState = FakeSocket.OPEN
    this.onopen?.()
  }

  /** 屏幕来一帧。 */
  frame(data: ArrayBuffer | string) {
    this.onmessage?.({ data } as MessageEvent)
  }

  /** 掉线。 */
  drop() {
    this.readyState = 3
    this.onclose?.()
  }
}

vi.stubGlobal('WebSocket', FakeSocket)

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  FakeSocket.instances = []
})

interface Fixture {
  status: Ref<DeviceScreenStatus>
  isOpen: () => boolean
  send: (data: string | Uint8Array) => void
  attached: number
  switched: number
  data: (Uint8Array | string)[]
  setSid: (sid: string) => void
  unmount: () => void
}

/** 摆成组件那个样子：先挂上，再由调用方 connect —— 组件是等画面准备好了才连的。 */
function mountScreen(sid = 'dev-1'): Fixture {
  const source = ref(sid)
  const target: Fixture = {
    status: ref<DeviceScreenStatus>('connecting'),
    isOpen: () => false,
    send: () => {},
    attached: 0,
    switched: 0,
    data: [],
    setSid: (next) => {
      source.value = next
    },
    unmount: () => {},
  }
  const { unmount } = render(
    defineComponent({
      setup() {
        const screen = useDeviceScreen(source, {
          onData: (chunk) => target.data.push(chunk),
          onAttached: () => {
            target.attached += 1
          },
          onSwitched: () => {
            target.switched += 1
          },
        })
        target.status = screen.status
        target.isOpen = screen.isOpen
        target.send = screen.send
        screen.connect(source.value)
        return () => h('div')
      },
    })
  )
  target.unmount = unmount
  return target
}

describe('现场 socket', () => {
  it('连着的时候是 connecting，连上了才是 open，并且那时候才发第一个尺寸', async () => {
    const screen = mountScreen()

    expect(screen.status.value).toBe('connecting')
    expect(screen.attached).toBe(0)
    expect(FakeSocket.last.url).toBe('ws://screen.test/connector/session/dev-1/screen')

    FakeSocket.last.connect()

    expect(screen.status.value).toBe('open')
    expect(screen.attached).toBe(1)
  })

  it('屏幕数据是字节、控制帧是字符串，原样交给画的那一半', async () => {
    const screen = mountScreen()
    FakeSocket.last.connect()

    const bytes = new Uint8Array([104, 105])
    FakeSocket.last.frame(bytes.buffer)
    FakeSocket.last.frame('hi')

    expect(screen.data).toHaveLength(2)
    expect(screen.data[0]).toBeInstanceOf(Uint8Array)
    expect(Array.from(screen.data[0] as Uint8Array)).toEqual([104, 105])
    expect(screen.data[1]).toBe('hi')
  })

  it('没连上时发不出去，连上之后发得出去', async () => {
    const screen = mountScreen()

    screen.send('resize')
    expect(FakeSocket.last.sent).toEqual([])

    FakeSocket.last.connect()
    screen.send('resize')
    expect(FakeSocket.last.sent).toEqual(['resize'])
  })

  it('换一台屏幕：旧的那条断掉，按新的地址重连，并叫调用方清缓冲', async () => {
    const screen = mountScreen()
    const first = FakeSocket.last
    first.connect()

    screen.setSid('dev-2')
    await nextTick()

    expect(screen.switched).toBe(1)
    expect(first.readyState).toBe(3)
    expect(FakeSocket.last).not.toBe(first)
    expect(FakeSocket.last.url).toBe('ws://screen.test/connector/session/dev-2/screen')
  })

  it('换过之后旧的那一条再掉线，不会把新画面的状态说成断的', async () => {
    const screen = mountScreen()
    const first = FakeSocket.last
    first.connect()

    screen.setSid('dev-2')
    await nextTick()
    FakeSocket.last.connect()

    first.drop()

    expect(screen.status.value).toBe('open')
  })

  it('掉线之后网络回来，自己接上', async () => {
    const screen = mountScreen()
    const first = FakeSocket.last
    first.connect()

    first.drop()
    expect(screen.status.value).toBe('closed')

    window.dispatchEvent(new Event('online'))
    await nextTick()

    expect(FakeSocket.last).not.toBe(first)
    expect(screen.status.value).toBe('connecting')
  })

  it('掉线时网络回来不会把还连着的那一条换掉', async () => {
    const screen = mountScreen()
    FakeSocket.last.connect()

    window.dispatchEvent(new Event('online'))
    await nextTick()

    expect(FakeSocket.instances).toHaveLength(1)
    expect(screen.status.value).toBe('open')
  })

  it('画面关掉之后 socket 也跟着收掉，不再占着那条连接', async () => {
    const screen = mountScreen()
    const first = FakeSocket.last
    first.connect()

    screen.unmount()

    expect(first.readyState).toBe(3)
    // 收掉的那一条再掉线，也不该转回来改这一页的状态。
    first.onclose?.()
    expect(screen.status.value).toBe('open')
  })
})
