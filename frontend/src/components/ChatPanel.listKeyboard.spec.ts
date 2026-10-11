// 键盘走过一列消息：整列只是一个 Tab 停靠点。
//
// 量过的毛病（dev，1440×900，真按 Tab）：每条消息是 7–14 个停靠点——行本身、头像、名字、
// 每个 @ 和文件、表情、支线那一行，亮着动作条时再加 6 颗按钮。5 条消息的频道从对话区
// 到输入框要按 32 下 Tab，60 条的任务要按 193 下；Shift+Tab 从输入框往回走和 Tab 往前
// 走停的还不是同一批地方。
//
// 这里钉的是人按键时看到的：Tab 一下进来、一下出去；↑↓ 在消息之间换；别的消息里的
// 按钮不在 Tab 序列里；Enter 进到当前那一条的按钮里，Esc 回到这一条；一个键从哪儿都
// 能回到输入框。happy-dom 不会自己按 Tab 移焦点，`press('Tab')` 照浏览器的规则走一步：
// 键先发给焦点所在的元素，没被拦下，就落到文档顺序里下一个停得住的地方。
import type { Router } from 'vue-router'
import type { Topic } from '../cx_types'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
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

import ChatPanel from './ChatPanel.vue'

import { installShortcuts } from '@/commands/shortcuts'
import i18n, { setLocale } from '@/i18n'

const topic: Topic = {
  id: 'room-keys',
  project_id: 'p1',
  parent_id: null,
  title: '房间',
  kind: 'topic',
  status: 'active',
  created_at: '2026-09-01T00:00:00Z',
} as Topic

/** 一条带着按钮的消息：正文里一个 @、一个文件，下面一颗表情。 */
function message(id: string, minute: number, author = '张衡') {
  return {
    id,
    conversation_id: topic.id,
    kind: 'message',
    author_type: 'participant' as const,
    author,
    content: `第 ${minute} 条，<@alice> 看看 <&src/a.ts>`,
    created_at: new Date(Date.UTC(2026, 8, 20, 0, minute)).toISOString(),
    reactions: [{ emoji: '👍', count: 1, authors: ['alice'] }],
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

const FOCUSABLE =
  'a[href], button, input, select, textarea, summary, iframe, [contenteditable]:not([contenteditable="false"]), [tabindex]'

/** 文档里此刻 Tab 停得住的地方，按文档顺序。 */
function tabStops(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) =>
      el.getAttribute('tabindex') !== '-1' &&
      !(el as HTMLButtonElement).disabled &&
      !el.closest('[inert], [hidden], [style*="display: none"]')
  )
}

/** 按一下键：先发给焦点所在的元素；是 Tab 而没被拦下，就照浏览器的规则挪一步焦点。 */
function press(key: string, init: KeyboardEventInit = {}) {
  const from = (document.activeElement as HTMLElement | null) ?? document.body
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
  const handled = !from.dispatchEvent(event)
  if (key !== 'Tab' || handled) return
  const stops = tabStops()
  const follows = (el: HTMLElement) => !!(from.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)
  const next = init.shiftKey
    ? stops.filter((el) => !follows(el) && el !== from && !el.contains(from)).at(-1)
    : stops.find((el) => follows(el) && !from.contains(el))
  next?.focus()
}

function active(): HTMLElement {
  return document.activeElement as HTMLElement
}

let stopShortcuts: (() => void) | undefined

beforeAll(() => {
  const g = globalThis as unknown as Record<string, unknown>
  g.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  g.WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
    send() {}
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }))
  listBlocks.mockResolvedValue({
    data: [message('a1', 1), message('a2', 2), message('a3', 3)],
    total: 3,
    has_more: false,
    oldest_id: null,
  })
  stopShortcuts = installShortcuts({ push: vi.fn() } as unknown as Router)
})
afterEach(() => {
  stopShortcuts?.()
  document.body.replaceChildren()
})

/** 对话前面有一颗按钮（页头上的东西），后面是输入框：Tab 有地方进来、有地方出去。 */
async function mount() {
  const vuetify = createVuetify({ components, directives })
  const Host = defineComponent(() => () => [
    h('button', { type: 'button', 'data-testid': 'before' }, '页头'),
    h(ChatPanel, { topic, topicList: [topic], showComposer: true }),
  ])
  const view = render(Host, { global: { plugins: [vuetify, i18n] } })
  await flush()
  const pane = view.container.querySelector<HTMLElement>('[data-testid="chat-scroll"]')!
  const row = (id: string) => view.container.querySelector<HTMLElement>(`[data-mid="${id}"]`)!
  const composer = () => view.container.querySelector<HTMLElement>('.composer-input textarea')!
  const before = view.getByTestId('before')
  return { view, pane, row, composer, before }
}

describe('一列消息只是一个 Tab 停靠点', () => {
  it('从输入框 Shift+Tab 一下停在最新一条上，再一下就出了这一列', async () => {
    const { pane, row, composer } = await mount()
    composer().focus()
    press('Tab', { shiftKey: true })
    expect(active()).toBe(row('a3'))
    press('Tab', { shiftKey: true })
    expect(pane.contains(active())).toBe(false)
  })

  it('从前面 Tab 一下停在最新一条上，再一下就到了这一列外面', async () => {
    const { pane, row, before } = await mount()
    before.focus()
    press('Tab')
    expect(active()).toBe(row('a3'))
    press('Tab')
    expect(pane.contains(active())).toBe(false)
  })

  it('不在当前那一条上的消息，里面的按钮、@ 和表情都不在 Tab 序列里', async () => {
    const { pane, row } = await mount()
    expect(tabStops().filter((el) => pane.contains(el))).toEqual([row('a3')])
  })

  it('再 Tab 进来，停在上次离开时的那一条上', async () => {
    const { row, composer } = await mount()
    composer().focus()
    press('Tab', { shiftKey: true })
    press('ArrowUp')
    expect(active()).toBe(row('a2'))
    composer().focus()
    press('Tab', { shiftKey: true })
    expect(active()).toBe(row('a2'))
  })
})

describe('在消息之间走', () => {
  it('↑ ↓ 换到上一条、下一条，Home / End 到第一条、最后一条', async () => {
    const { row, before } = await mount()
    before.focus()
    press('Tab')
    press('ArrowUp')
    expect(active()).toBe(row('a2'))
    press('ArrowUp')
    expect(active()).toBe(row('a1'))
    press('ArrowDown')
    expect(active()).toBe(row('a2'))
    press('End')
    expect(active()).toBe(row('a3'))
    press('Home')
    expect(active()).toBe(row('a1'))
  })

  it('走到哪一条，Tab 序列里就只剩哪一条', async () => {
    const { pane, row, before } = await mount()
    before.focus()
    press('Tab')
    press('ArrowUp')
    await flush()
    expect(tabStops().filter((el) => pane.contains(el))).toEqual([row('a2')])
  })

  it('读屏读到的一条消息，先是谁、几点', async () => {
    const { row } = await mount()
    expect(row('a2').getAttribute('role')).toBe('article')
    expect(row('a2').getAttribute('aria-label')).toMatch(/^张衡，[^：]+：第 2 条/)
  })
})

describe('进到一条消息的按钮里', () => {
  it('Enter 进到这一条的动作条，Tab 接着走到这一行里的 @，Esc 回到这一条', async () => {
    const { pane, row, before } = await mount()
    before.focus()
    press('Tab')
    press('ArrowUp')
    press('Enter')
    await flush()
    const bar = pane.querySelector('.hover-bar')!
    expect(bar.contains(active())).toBe(true)
    expect(active().getAttribute('aria-label')).toBe('添加表情')

    // 走过动作条上的几颗，下一下落在这一行正文里的 @ 上——还是 a2 这一条。
    for (let i = 0; i < 12 && bar.contains(active()); i += 1) press('Tab')
    expect(row('a2').contains(active())).toBe(true)
    expect(active().matches('.mention')).toBe(true)

    press('Escape')
    expect(active()).toBe(row('a2'))
  })

  it('这一条的按钮走完，下一下 Tab 就出了这一列，不进别的消息', async () => {
    const { pane, row, before } = await mount()
    before.focus()
    press('Tab')
    press('ArrowUp')
    press('Enter')
    await flush()
    const visited: HTMLElement[] = []
    for (let i = 0; i < 30 && pane.contains(active()); i += 1) {
      visited.push(active())
      press('Tab')
    }
    expect(pane.contains(active())).toBe(false)
    // 中途停过的，都是 a2 自己的（它的动作条也算）。
    const bar = pane.querySelector('.hover-bar')!
    expect(visited.every((el) => row('a2').contains(el) || bar.contains(el))).toBe(true)
    expect(visited.some((el) => row('a2').contains(el) && el.matches('.rx-chip'))).toBe(true)
  })
})

describe('回到输入框的快捷键', () => {
  it('焦点在一条消息上，Alt+Shift+C 把它送到输入框', async () => {
    const { row, composer, before } = await mount()
    before.focus()
    press('Tab')
    expect(active()).toBe(row('a3'))
    press('C', { code: 'KeyC', altKey: true, shiftKey: true })
    await flush()
    expect(active()).toBe(composer())
  })

  // 搜索面板、对话框开着时焦点在它里面：拉到背后的输入框，浮层还开着、Esc 也关不掉它。
  it('焦点在一个开着的浮层里时不动', async () => {
    await mount()
    const overlay = document.createElement('div')
    overlay.className = 'v-overlay v-overlay--active'
    const input = document.createElement('input')
    overlay.append(input)
    document.body.append(overlay)
    input.focus()
    press('C', { code: 'KeyC', altKey: true, shiftKey: true })
    await flush()
    expect(active()).toBe(input)
    overlay.remove()
  })
})
