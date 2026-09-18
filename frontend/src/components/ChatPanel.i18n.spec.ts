/** 这条对话面板整块跟着语言走。
 *
 * 这一份真的把面板挂起来渲染一遍，扫的是**整块面板**：空房间的开场白和那三张
 * 起手卡片、输入栏的 placeholder / title、时间线上那两条日期刻度、以及每条消息
 * hover 才浮出来的三颗动作按钮。这些字一半在正文里、一半只在 `title` 上，所以
 * 这里可以整块扫「一个汉字都不剩」——不像整页，页面上还有别的切片欠着的字。
 *
 * 有一条边界这里守得住：**「交给芝士」那句话里的名字是两个人的名字**——正文里
 * 说「交给 Cheese」，旁边那颗按钮写的是同一件事的短句。两句走两个键
 * （`handToAgent` / `handToAgentShort`），改了一处忘另一处，至少红一条。
 *
 * 另一条：**动作卡上的按钮文案跟着资源走**。`doc` / `decision` / `milestone` 三张
 * 卡各说各的（View document / View decisions / View calendar），而键是查表得来的
 * （`ACTION_META[...].btnKey`），不是字面量——类型检查和词表闸门都看不见一个查表
 * 查空的键，只有真渲染一遍才知道它会不会把键名原样吐在按钮上。
 *
 * 扫的是 `document.body`：菜单/浮层挂在 teleport 出去的容器上，不在挂载点里。
 */
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    listBlocks: (...a: unknown[]) => listBlocks(...a),
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    // 房间里那位芝士。名字是**名册上的数据**，不是文案——所以这里给个 ASCII 的
    // 名字，扫汉字的时候才扫得准（真实环境里它就叫「芝士」，那是数据不是词表）。
    listTopicMembers: vi.fn().mockResolvedValue({
      data: [
        { id: 'm1', member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
        { id: 'm2', member_handle: 'cheese-t1', name: 'Cheese', role: 'member', agent: true },
      ],
      total: 2,
    }),
    chatWsUrl: () => 'ws://test/ws',
    attachmentRawUrl: () => '',
    answerOptions: vi.fn(),
    toggleReaction: vi.fn(),
  }
})

import ChatPanel from './ChatPanel.vue'

import { setLocale } from '@/i18n'

const Panel = ChatPanel as unknown as Component

/** 这个类里有 U+8C48–U+FAFF 那一段，它把 UTF-16 代理对的一半也算成「汉字」——
 *  所以非 BMP 的 emoji 会误报。这一列里没有 emoji，正好当闸门用。 */
const CJK = /[㐀-䶿一-鿿豈-﫿]/

let roomSeq = 0
function room(kind = 'topic'): Topic {
  roomSeq += 1
  return {
    id: `i18n-room-${roomSeq}`,
    project_id: 'p1',
    parent_id: null,
    title: 't',
    kind,
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  } as Topic
}

function msg(id: string, at: Date, content = id): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'human',
    author: 'alice',
    content,
    reply_to: null,
    refs: [],
    created_at: at.toISOString(),
  } as unknown as Block
}

function event(id: string, content: string, meta: Record<string, unknown> | null): Block {
  return {
    id,
    project_id: 'p1',
    topic_id: 't1',
    kind: 'event',
    author_type: 'system',
    author: 'system',
    content,
    meta,
    refs: [],
    created_at: '2026-08-15T10:00:00Z',
  } as unknown as Block
}

const daysAgo = (n: number, h = 9) => {
  const d = new Date()
  d.setDate(d.getDate() - n)
  d.setHours(h, 30, 0, 0)
  return d
}

const flush = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 手机宽度：Vuetify 的断点读的是 window.innerWidth，而「照片」那一颗只在手机
 *  上摆（桌面上截图一贴、文件一拖就完事了）。 */
function mount(topic: Topic, blocks: Block[] = [], alwaysSummon = false) {
  Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true })
  listBlocks.mockResolvedValue({ data: blocks, total: blocks.length, has_more: false })
  const vuetify = createVuetify({ components, directives })
  return render(Panel, {
    props: {
      topic,
      showComposer: true,
      hideHeader: true,
      alwaysSummon,
      members: [{ user_handle: 'alice', name: 'Alice', role: 'lead' }],
    },
    global: { plugins: [vuetify] },
  })
}

/** 扫 `root` 里所有看得见的字，外加 title / aria-label 上的字。空清单才算过——
 *  报错信息里带上整句，红的时候不用再猜是哪一处。 */
function cjkHits(root: ParentNode): string[] {
  const hits: string[] = []
  const text = (root as Element).textContent ?? ''
  if (CJK.test(text)) hits.push(`textContent=${text.replace(/\s+/g, ' ').slice(0, 400)}`)
  for (const el of Array.from(root.querySelectorAll('[title], [aria-label]'))) {
    for (const attr of ['title', 'aria-label']) {
      const value = el.getAttribute(attr) ?? ''
      if (CJK.test(value)) hits.push(`${attr}=${value}`)
    }
  }
  return hits
}

function startLabels(container: Element): string[] {
  return Array.from(container.querySelectorAll('.chat-start .v-btn')).map((el) => el.textContent?.trim() ?? '')
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 390,
      height: 800,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
})

beforeEach(() => {
  vi.clearAllMocks()
  listBlocks.mockReset()
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
})

describe('讲中文', () => {
  it('空房间的开场白、三张起手卡片和输入栏都是中文', async () => {
    setLocale('zh-CN')
    const { container } = mount(room('root'))
    await flush()

    const start = container.querySelector('.chat-start') as HTMLElement
    expect(start.querySelector('.t-title')?.textContent?.trim()).toBe('从一件具体的事开始')
    expect(start.textContent).toContain('@芝士')
    expect(startLabels(container)).toEqual(['查找资料', '起草文档', '拆解任务'])
    expect(container.querySelector('.t-meta')?.textContent?.trim()).toBe('点选后补充你的需求，再发送')

    const box = container.querySelector<HTMLTextAreaElement>('.composer textarea')!
    expect(box.getAttribute('placeholder')).toBe('输入消息，@Cheese 交给它做')
    expect(box.getAttribute('title')).toContain('Enter 发送')
    expect(container.querySelector('[title="上传文件（每个最大 10MB）"]')).not.toBeNull()
    expect(container.querySelector('[title="发送照片"]')).not.toBeNull()
    expect(container.querySelector('[title="发送"]')).not.toBeNull()
    expect(container.querySelector('.summon-btn-label')?.textContent?.trim()).toBe('交给Cheese')
  })
})

describe('讲英文', () => {
  it('空房间整块一个汉字都不剩', async () => {
    setLocale('en')
    const { container } = mount(room('root'))
    await flush()

    const start = container.querySelector('.chat-start') as HTMLElement
    expect(start.querySelector('.t-title')?.textContent?.trim()).toBe('Start with something concrete')
    expect(start.textContent).toContain('@Cheese')
    expect(startLabels(container)).toEqual(['Find sources', 'Draft a document', 'Break down a goal'])
    expect(container.querySelector('.t-meta')?.textContent?.trim()).toBe('Pick one, add your details, then send')

    const box = container.querySelector<HTMLTextAreaElement>('.composer textarea')!
    expect(box.getAttribute('placeholder')).toBe('Type a message, @Cheese to hand it over')
    expect(box.getAttribute('title')).toContain('Enter to send')
    expect(container.querySelector('[title="Upload files (10MB max each)"]')).not.toBeNull()
    expect(container.querySelector('[title="Send a photo"]')).not.toBeNull()
    expect(container.querySelector('[title="Send"]')).not.toBeNull()
    expect(container.querySelector('.summon-btn-label')?.textContent?.trim()).toBe('Hand to Cheese')

    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })

  it('时间线上的刻度、和每条消息 hover 才浮出来的那三颗', async () => {
    setLocale('en')
    const { container } = mount(room(), [msg('older', daysAgo(1)), msg('today', daysAgo(0))])
    await flush()

    const marks = Array.from(container.querySelectorAll('.tl-mark')).map((n) => n.textContent?.trim())
    expect(marks).toEqual(['Yesterday', 'Today'])

    const actions = Array.from(container.querySelectorAll('.im-act')).map((el) => el.getAttribute('title'))
    expect(actions).toContain('Reply')
    expect(actions).toContain('Upgrade to a topic')
    expect(actions).toContain('Add reaction')

    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })

  it('动作卡上那颗按钮按资源说话 —— 查表的键不会原样吐在按钮上', async () => {
    setLocale('en')
    // 卡片那句话是后端写进块内容里的**数据**，所以这里给英文，好让整块扫得干净。
    const { container } = mount(room(), [
      event('a', 'Cheese updated the topic doc', { action: 'doc' }),
      event('b', 'Cheese recorded a decision', { action: 'decision' }),
      event('c', 'Cheese scheduled a milestone', { action: 'milestone' }),
    ])
    await flush()

    const buttons = Array.from(container.querySelectorAll('.action-card button')).map((el) => el.textContent?.trim())
    expect(buttons).toEqual(['View document', 'View decisions', 'View calendar'])

    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })
})

describe('切一次语言', () => {
  it('已经画出来的开场白和输入栏当场跟着换', async () => {
    setLocale('zh-CN')
    const { container } = mount(room('root'))
    await flush()
    expect(container.querySelector('.chat-start .t-title')?.textContent?.trim()).toBe('从一件具体的事开始')

    setLocale('en')
    await vi.waitFor(() =>
      expect(container.querySelector('.chat-start .t-title')?.textContent?.trim()).toBe('Start with something concrete')
    )
    expect(startLabels(container)).toEqual(['Find sources', 'Draft a document', 'Break down a goal'])
    // 输入栏那句是 computed 出来的：换语言时它得跟着重算，不是挂载时算一次就完
    const box = container.querySelector<HTMLTextAreaElement>('.composer textarea')!
    expect(box.getAttribute('placeholder')).toBe('Type a message, @Cheese to hand it over')
    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })

  // 私聊那一版提示语是另一句：那边的芝士没有工具，读不了文件也跑不了命令，说的
  // 是「聊聊」而不是「交给它做」。两个键各走各的，别让一处改了另一处不动。
  it('私聊那一版是另一句提示语', async () => {
    setLocale('en')
    const { container } = mount(room('topic'), [], true)
    await flush()
    const box = container.querySelector<HTMLTextAreaElement>('.composer textarea')!
    expect(box.getAttribute('placeholder')).toBe('Chat with Cheese, or hand over something to do…')
    // 私聊里那颗「交给」按钮不摆：那条消息本来就一定叫得动它
    expect(container.querySelector('.summon-btn')).toBeNull()
    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })
})
