// 从一条旧消息打开对话（搜索结果、别人发来的链接）：停在那一条上；往回看的时候别人
// 说的话不会插进眼前这一段，但「回到最新」一点就到，不用再等一次加载。
import type { Component } from 'vue'
import type { BlockPage } from '../api'
import type { Block, Topic } from '../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/roomLink', () => import('@/test/fakeRoomLink'))
vi.mock('../api', async () => ({
  ...(await vi.importActual<typeof import('../api')>('../api')),
  listBlocks: vi.fn(),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [] }),
  listTopicMembers: vi.fn().mockResolvedValue({ data: [] }),
}))
// 发出去的那条还在路上：它先以待发的样子出现在最底下。
vi.mock('../api/messages', () => ({ postChatMessage: vi.fn(() => new Promise(() => {})) }))

import { listBlocks } from '../api'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale, t } from '@/i18n'

const sockets: TestSocket[] = []
class TestSocket {
  static OPEN = 1
  readyState = 1
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  constructor() {
    sockets.push(this)
  }
}

const flush = () => vi.advanceTimersByTimeAsync(0)

function said(id: string, author = 'someone-else'): Block {
  return {
    id,
    conversation_id: 't',
    kind: 'message',
    author_type: 'participant',
    author,
    content: `message ${id}`,
    created_at: new Date().toISOString(),
  } as Block
}

function page(ids: string[], more: { older: boolean; newer: boolean }): BlockPage {
  return {
    data: ids.map((id) => said(id)),
    total: 100,
    has_more: more.older,
    oldest_id: ids[0] ?? null,
    has_newer: more.newer,
    newest_id: ids.at(-1) ?? null,
  }
}

// 房间里最新的一页是 n1–n3；o4–o6 是很早以前的一段，不在最新一页里。每次现造：
// 对话栏会往拿到的那一页里接新消息。
const NEWEST = () => page(['n1', 'n2', 'n3'], { older: true, newer: false })
const AROUND_O5 = () => page(['o4', 'o5', 'o6'], { older: true, newer: true })

function arrive(block: Block) {
  sockets[0].onmessage?.({ data: JSON.stringify({ type: 'user_block', block }) })
}

const shown = (container: Element) =>
  Array.from(container.querySelectorAll('[data-mid]')).map((el) => el.getAttribute('data-mid'))
const pill = (container: Element) => container.querySelector('.new-pill')

// happy-dom 不排版：给滚动容器一个高度，再把它停在最底下，然后把那条滚动事件补上。
// 「在不在底部」（药丸露不露面读的就是它）是在这条事件里重新量出来的
// （useChatScroll.rememberScroll），真浏览器里落点之后一定会到。
function settleAtBottom(pane: HTMLElement) {
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, value: 1000 })
  Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 600 })
  pane.scrollTop = 400
  pane.dispatchEvent(new Event('scroll'))
}

let rooms = 0
async function mountRoom(focusBlock: string | null) {
  const focus = ref(focusBlock)
  const panel = ref<{ send: (content: string, summon: boolean) => boolean } | null>(null)
  const Host = defineComponent({
    setup: () => () =>
      h(ChatPanel as unknown as Component, {
        ref: panel,
        // 每条用例一个新房间：发件箱和缓存按房间存在整个应用里，上一条留下的别带过来。
        topic: { id: `t${++rooms}`, project_id: 'p', title: 'Room', kind: 'topic' } as Topic,
        focusBlock: focus.value,
      }),
  })
  const view = render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  await flush()
  sockets[0].onopen?.()
  await vi.advanceTimersByTimeAsync(500)
  return { ...view, focus, panel }
}

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  vi.useFakeTimers()
  sockets.length = 0
  vi.stubGlobal('WebSocket', TestSocket)
  Element.prototype.scrollIntoView = vi.fn()
  vi.mocked(listBlocks)
    .mockReset()
    .mockImplementation(async (_topic, opts) => {
      if (opts?.around === 'o5') return AROUND_O5()
      // 往上、往下再翻都到头了：这几条用例只关心上面两段。
      if (opts?.before) return page([], { older: false, newer: true })
      if (opts?.after) return page([], { older: true, newer: false })
      return NEWEST()
    })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('从一条旧消息打开对话', () => {
  it('停在点名的那一条上，显示它前后的一段，而不是最新的一页', async () => {
    const { container } = await mountRoom('o5')
    expect(shown(container)).toEqual(['o4', 'o5', 'o6'])
    expect(pill(container)?.textContent).toContain(t('work.room.backToLatest'))
  })

  it('停在中间时别人说的话不插进眼前这一段，提示数得出来了几条', async () => {
    const { container } = await mountRoom('o5')
    arrive(said('n4'))
    await flush()
    expect(shown(container)).toEqual(['o4', 'o5', 'o6'])
    expect(pill(container)?.textContent).toContain('1')
  })

  it('点「回到最新」直接回到最新一页，连同刚才来的那条，不再发一次请求', async () => {
    const { container } = await mountRoom('o5')
    arrive(said('n4'))
    await flush()
    const requests = vi.mocked(listBlocks).mock.calls.length
    await fireEvent.click(pill(container)!)
    await flush()
    expect(shown(container)).toEqual(['n1', 'n2', 'n3', 'n4'])
    expect(vi.mocked(listBlocks).mock.calls.length).toBe(requests)
    expect(pill(container)).toBeNull()
  })

  it('停在中间时自己发一条，回到最新，看得见刚发的', async () => {
    const { container, panel } = await mountRoom('o5')
    panel.value!.send('我来补充一下', false)
    await flush()
    expect(shown(container).slice(0, 3)).toEqual(['n1', 'n2', 'n3'])
    expect(container.textContent).toContain('我来补充一下')
  })

  it('浏览器后退到跳过来之前（地址不再点名），回到最新', async () => {
    const { container, focus } = await mountRoom('o5')
    focus.value = null
    await flush()
    expect(shown(container)).toEqual(['n1', 'n2', 'n3'])
  })

  it('点名的消息就在最新一页里：原地停过去，不另取一段', async () => {
    const { container, getByTestId } = await mountRoom('n2')
    expect(shown(container)).toEqual(['n1', 'n2', 'n3'])
    expect(vi.mocked(listBlocks).mock.calls.some(([, opts]) => opts?.around)).toBe(false)
    // n2 就是最底下那一条：落过去之后人还在底部，入口收起来。
    settleAtBottom(getByTestId('chat-scroll'))
    await flush()
    expect(pill(container)).toBeNull()
  })
})
