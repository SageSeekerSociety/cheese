// 键盘把聊天容器挤矮时，停在底部的人要被重新钉在底部。
//
// 这一条守的是一条只在手机上出现的路径：`--keyboard-inset` 让外壳变矮，缩掉的是
// 滚动**容器**（`.v-application__wrap` 是它的祖先），时间线内容的高度一个像素都
// 没变。ResizeObserver 只盯内容时，键盘一起来它一次回调都不发，最新几条就滑到
// 键盘底下（真机反馈 2026-09-17）。
//
// happy-dom 不做布局：`scrollHeight` / `clientHeight` 恒为 0，ResizeObserver 也
// 没有实现。所以三个尺寸由用例自己按上去，ResizeObserver 由这个文件自己实现——
// 「发一次回调」于是变成一个用例能做的动作，而不是要等一次真的布局。
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listBlocks: (...args: unknown[]) => listBlocks(...args),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    chatWsUrl: () => 'ws://test/chat',
  }
})

import ChatPanel from './ChatPanel.vue'

const topic: Topic = {
  id: 'keyboard-scroll-topic',
  project_id: 'p1',
  parent_id: null,
  title: 'Keyboard scroll',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-17T00:00:00Z',
  updated_at: '2026-09-17T00:00:00Z',
} as Topic

const block: Block = {
  id: 'm1',
  topic_id: topic.id,
  kind: 'message',
  author_type: 'ai',
  author: 'cheese-keyboard',
  content: '一条消息',
  created_at: '2026-09-17T00:00:01Z',
} as Block

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = []

  targets: Element[] = []
  private readonly cb: ResizeObserverCallback

  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
    FakeResizeObserver.instances.push(this)
  }

  observe(el: Element) {
    this.targets.push(el)
  }

  unobserve() {}

  disconnect() {
    this.targets = []
  }

  fire() {
    this.cb([], this as unknown as ResizeObserver)
  }
}

function observerWatching(el: Element): FakeResizeObserver | undefined {
  return FakeResizeObserver.instances.find((o) => o.targets.includes(el))
}

type Metrics = { scrollHeight: number; clientHeight: number; scrollTop: number }

/** happy-dom 的元素没有布局：这三个数是浏览器算出来的，这里按用例的意思钉住。 */
function fakeMetrics(el: HTMLElement, initial: Metrics): Metrics {
  const m = { ...initial }
  for (const key of ['scrollHeight', 'clientHeight', 'scrollTop'] as const) {
    Object.defineProperty(el, key, {
      configurable: true,
      get: () => m[key],
      set: (v: number) => {
        m[key] = v
      },
    })
  }
  return m
}

async function flush() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

async function mount() {
  const vuetify = createVuetify({ components, directives })
  const view = render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [vuetify] },
  })
  await flush()
  const pane = view.container.querySelector('[data-testid="chat-scroll"]') as HTMLElement
  return { view, pane }
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeResizeObserver.instances = []
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 1
      onmessage: unknown = null
      onopen: unknown = null
      onclose: unknown = null
      onerror: unknown = null
      close() {}
      send() {}
    }
  )
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }))
  listBlocks.mockResolvedValue({ data: [block], has_more: false })
})

describe('键盘挤矮聊天容器', () => {
  it('容器变矮时把停在底部的人重新钉到底部', async () => {
    const { pane } = await mount()
    expect(observerWatching(pane), '滚动容器上要挂着一个 ResizeObserver').toBeTruthy()
    const m = fakeMetrics(pane, { scrollHeight: 1000, clientHeight: 500, scrollTop: 500 })

    // 键盘弹出来：看得见的地方矮了 300，内容一点没变。
    m.clientHeight = 200
    observerWatching(pane)!.fire()
    await flush()

    expect(pane.scrollTop).toBe(1000)
  })

  it('用户自己上滚看历史时，容器变矮不把他拽回底部', async () => {
    const { pane } = await mount()
    const m = fakeMetrics(pane, { scrollHeight: 1000, clientHeight: 500, scrollTop: 100 })

    // 上滚过，`atBottom` 因此是假的。
    pane.dispatchEvent(new Event('scroll'))
    await flush()

    m.clientHeight = 200
    observerWatching(pane)!.fire()
    await flush()

    expect(pane.scrollTop).toBe(100)
  })
})
