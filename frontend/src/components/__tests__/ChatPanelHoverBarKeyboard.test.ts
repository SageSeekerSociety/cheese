// 键盘够得着消息的动作条：焦点落在一条消息上就亮出这条动作条，Enter 从行走进条里的
// 按钮，Esc / 焦点离开整列再收起——收起的只是条，人还停在原来那一条上。在消息之间怎么
// 走、Tab 怎么进出这一列，见 ChatPanel.listKeyboard.spec.ts。
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('../../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../../lib/libraryApi')>('../../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listTopicMembers: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    attachmentRawUrl: () => '',
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from '../ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { loadMarkdownReader } from '@/test/markdownReader'

const topic: Topic = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '房间',
  kind: 'topic',
  status: 'active',
  created_at: '2026-09-01T00:00:00Z',
} as Topic

function message(id: string, minute: number) {
  return {
    id,
    conversation_id: topic.id,
    kind: 'message',
    author_type: 'participant' as const,
    author: '张衡',
    content: `第 ${minute} 条`,
    created_at: new Date(Date.UTC(2026, 8, 20, 0, minute)).toISOString(),
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function row(container: Element, id: string): HTMLElement {
  return container.querySelector<HTMLElement>(`[data-mid="${id}"]`)!
}
function bar(container: Element): HTMLElement {
  const el = container.querySelector<HTMLElement>('.hover-bar')
  if (!el) throw new Error('no .hover-bar')
  return el
}
function barCount(container: Element): number {
  return container.querySelectorAll('.hover-bar').length
}
function barShown(container: Element): boolean {
  return bar(container).getAttribute('aria-hidden') === 'false'
}
/** 让下一下算作键盘：行可聚焦之后，焦点只有来自键盘才亮动作条（指针点一行不算）。 */
function useKeyboard() {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }))
}
/** 不是这一列自己用的键：音量键/媒体键、F1–F12、字母都算。 */
function pressOther(key: string) {
  window.dispatchEvent(new KeyboardEvent('keydown', { key }))
}
/** 指针按下还没抬起：浏览器此时把焦点放到指针下的那一行上，就是点一条消息的那一下。 */
function pointerDown() {
  window.dispatchEvent(new Event('pointerdown'))
}
function pointerUp() {
  window.dispatchEvent(new Event('pointerup'))
}
/** 点一条消息：按下、聚焦、抬起，和浏览器里的顺序一样。 */
function clickRow(el: HTMLElement) {
  pointerDown()
  el.focus()
  pointerUp()
}
/** 时间线上是不是挂着「用键盘走这一列」那个类：行上的焦点框就靠它。 */
function ringOn(container: Element): boolean {
  return container.querySelector('[data-chat-feed]')!.classList.contains('tl-feed--kbd')
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  listBlocks.mockResolvedValue({
    data: [message('a1', 1), message('a2', 2)],
    total: 2,
    has_more: false,
    oldest_id: null,
  })
})

async function mount() {
  const vuetify = createVuetify({ components, directives })
  const view = render(ChatPanel, {
    props: { topic, topicList: [topic] },
    global: { plugins: [vuetify, i18n] },
  })
  await flush()
  return view
}

beforeAll(loadMarkdownReader)

describe('消息动作条的键盘可达性', () => {
  it('消息行可以聚焦，且不真的多出一条动作条', async () => {
    const { container } = await mount()
    expect(barCount(container)).toBe(1)
    row(container, 'a1').focus()
    expect(document.activeElement).toBe(row(container, 'a1'))
    expect(barCount(container)).toBe(1)
  })

  it('焦点落在一条消息上，这条动作条亮出来并停在它上面', async () => {
    const { container } = await mount()
    expect(barShown(container)).toBe(false)
    useKeyboard()
    row(container, 'a2').focus()
    await flush()
    expect(barShown(container)).toBe(true)
    // 收起时只有一条，亮出来也还是同一条。
    expect(barCount(container)).toBe(1)
  })

  it('Enter 从一行走进这条动作条里的按钮', async () => {
    const { container } = await mount()
    const r = row(container, 'a1')
    useKeyboard()
    r.focus()
    await flush()
    expect(barShown(container)).toBe(true)
    fireEvent.keyDown(r, { key: 'Enter' })
    await flush()
    const active = document.activeElement as HTMLElement
    expect(bar(container).contains(active)).toBe(true)
    expect(active).toBe(bar(container).querySelector('button'))
  })

  it('Esc 收起动作条，但人还留在那一条消息上', async () => {
    const { container } = await mount()
    const r = row(container, 'a1')
    useKeyboard()
    r.focus()
    await flush()
    fireEvent.keyDown(r, { key: 'Enter' })
    await flush()
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    await flush()
    expect(barShown(container)).toBe(false)
    expect(document.activeElement).toBe(r)
  })

  it('焦点离开整列才收起，行与行之间不收起', async () => {
    const { container } = await mount()
    const a1 = row(container, 'a1')
    useKeyboard()
    a1.focus()
    await flush()
    expect(barShown(container)).toBe(true)
    row(container, 'a2').focus()
    await flush()
    expect(barShown(container)).toBe(true)
  })
})

describe('只有这一列自己用的键才算「在用键盘」', () => {
  it('点过一条消息再按音量键，不算在用键盘走这一列', async () => {
    const { container } = await mount()
    clickRow(row(container, 'a1'))
    await flush()
    expect(barShown(container)).toBe(false)

    // 音量键：有的系统会把它也递进网页。它不该让点过的那一条算成键盘停在上面。
    pressOther('AudioVolumeUp')
    await flush()
    expect(barShown(container)).toBe(false)

    // 改之前，上面那一下会让下面这次换焦点亮出动作条。
    row(container, 'a2').focus()
    await flush()
    expect(barShown(container)).toBe(false)
  })

  it('点一条消息不画焦点框；键盘走过来画', async () => {
    const { container } = await mount()
    clickRow(row(container, 'a1'))
    await flush()
    expect(ringOn(container)).toBe(false)
    expect(document.activeElement).toBe(row(container, 'a1'))

    useKeyboard()
    row(container, 'a2').focus()
    await flush()
    expect(ringOn(container)).toBe(true)
  })

  it('指针只是按下、没碰到消息行时，已经画着的焦点框留着', async () => {
    const { container } = await mount()
    useKeyboard()
    const r = row(container, 'a1')
    r.focus()
    await flush()
    expect(ringOn(container)).toBe(true)

    // 拖滚动条、点在行与行的空隙上：焦点没动，框该留着。
    pointerDown()
    await flush()
    expect(document.activeElement).toBe(r)
    expect(ringOn(container)).toBe(true)
  })

  it('没有按键参与、直接聚焦到一条消息（读屏、脚本、浏览器把焦点还回来）也画框', async () => {
    const { container } = await mount()
    row(container, 'a1').focus()
    await flush()
    expect(ringOn(container)).toBe(true)
  })

  it('指针没抬起（被系统取消）之后，键盘聚焦照旧画框', async () => {
    const { container } = await mount()
    pointerDown()
    window.dispatchEvent(new Event('pointercancel'))
    await flush()
    useKeyboard()
    row(container, 'a1').focus()
    await flush()
    expect(ringOn(container)).toBe(true)
  })
})
