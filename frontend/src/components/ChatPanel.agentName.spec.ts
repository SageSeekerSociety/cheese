// 对话里 AI 说的每一句话上写的名字。
//
// 一个项目可以有好几个 AI 队友，一个房间随时能换。这里的名字曾经是一个写死的
// 「芝士」——换完队友，名册上改了、气泡上没改，看起来就是「换人根本没生效」，
// 而屏幕上没有任何东西说明这两个名字为什么不一样。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale, t } from '@/i18n'

// 发出去的消息走 POST，这里把每一次的正文记下来——「@ 的是谁」最终就落在正文里。
const sent = vi.hoisted(() => [] as { content: string }[])
vi.mock('../api/messages', async () => {
  const actual = await vi.importActual<typeof import('../api/messages')>('../api/messages')
  return {
    ...actual,
    postChatMessage: vi.fn(async (topicId: string, body: { content: string; request_id: string }) => {
      sent.push({ content: body.content })
      return {
        id: body.request_id,
        conversation_id: topicId,
        kind: 'message',
        author_type: 'participant',
        author: 'me',
        content: body.content,
        meta: { client_id: body.request_id },
        created_at: new Date().toISOString(),
      }
    }),
  }
})

const Panel = ChatPanel as unknown as Component

const SEAT = 'cheese-t1'

function topicOf(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: id,
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  } as Topic
}

function aiMsg(id: string, content = id, author: string = SEAT): Block {
  return {
    id,
    project_id: 'p1',
    conversation_id: 't1',
    kind: 'message',
    author_type: 'participant',
    author,
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []
/** 名册上芝士那一行的名字 —— 后端把它解析成这个房间当前的队友。 */
let agentName = '芝士'

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  history = []
  agentName = '芝士'
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
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () => {
      const u = String(url)
      if (u.includes('/members'))
        return {
          code: 200,
          data: {
            data: [
              { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
              { id: '2', member_handle: SEAT, name: agentName, role: 'member', agent: true },
            ],
            total: 2,
          },
        }
      if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
      if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
      return { code: 200, data: { data: history, total: history.length, has_more: false } }
    },
  }))
})

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('AI 说的话署谁的名', () => {
  it('名册晚到时，已有消息里的点名也更新为显示名', async () => {
    const originalFetch = globalThis.fetch
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal('fetch', async (...args: Parameters<typeof fetch>) => {
      if (String(args[0]).includes('/members')) await gate
      return originalFetch(...args)
    })
    history = [{ ...aiMsg('mention-late', `<@${SEAT}> 请整理方案`), author_type: 'participant', author: 'me' }]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: false },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()
    expect(container.querySelector('.im-text .mention')?.textContent).toBe(`@${SEAT}`)
    release()
    await settle()
    expect(container.querySelector('.im-text .mention')?.textContent).toBe('@芝士')
  })

  it('署这个房间现在交给的那个队友，不是写死的「芝士」', async () => {
    agentName = '评审'
    history = [aiMsg('a', '看过了')]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()

    const names = Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())
    expect(names).toContain('评审')
    expect(names).not.toContain('芝士')
  })

  it('名册上没有的 AI 作者，署「芝士」，不摆出 handle', async () => {
    // 它已经不在这个房间了（被移出），或者这是人和人的私聊里平台自己写的一条。
    // `cheese-<hex>` 是管道，读的人只会当成乱码。
    history = [aiMsg('a', '当时是我说的', 'cheese-0badc0ffee11')]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()

    const names = Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())
    expect(names).toContain('芝士')
    expect(names.some((n) => n?.startsWith('cheese-'))).toBe(false)
  })

  it('loads the new room roster when navigating between topics', async () => {
    history = [aiMsg('a', '看过了')]
    const { container, rerender } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()
    expect(Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())).toContain('芝士')

    agentName = '评审'
    await rerender({ topic: topicOf('t2'), showComposer: true })
    await settle()

    const names = Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())
    expect(names).toContain('评审')
    expect(names).not.toContain('芝士')
  })

  it('房间里没有 AI 席位时，落到项目的默认队友，不是名册上第一位', async () => {
    // 老房间（席位是后来才有的）。后端给这样一间房解析出来的是项目的**默认**队
    // 友，所以界面也只能读那一位：名册上第一个带 AI 标的是建得最早的那一位，而
    // 停用默认队友会把默认改判给另一位——照第一位写名字，答话的是别人。
    vi.stubGlobal('fetch', async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => {
        const u = String(url)
        if (u.includes('/members'))
          return {
            code: 200,
            data: { data: [{ id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false }], total: 1 },
          }
        if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
        if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
        return { code: 200, data: { data: history, total: history.length, has_more: false } }
      },
    }))
    history = []
    const { container } = render(Panel, {
      props: {
        topic: topicOf('t1'),
        showComposer: true,
        members: [
          { user_handle: 'cheese-oldest', role: 'member', name: '退休', agent: true, project_default: false },
          { user_handle: 'cheese-onduty', role: 'member', name: '接班', agent: true, project_default: true },
        ],
      },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()

    const shown = container.textContent ?? ''
    expect(shown).toContain(t('work.room.composer.summon', { name: '接班' }))
    expect(shown).not.toContain('退休')
  })

  it('名册还没到，也不能空着 —— 退回「芝士」', async () => {
    vi.stubGlobal('fetch', async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => {
        const u = String(url)
        if (u.includes('/members')) throw new Error('boom')
        if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
        if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
        return { code: 200, data: { data: history, total: history.length, has_more: false } }
      },
    }))
    history = [aiMsg('a', '看过了')]
    const { container } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()
    expect(Array.from(container.querySelectorAll('.im-name')).map((n) => n.textContent?.trim())).toContain('芝士')
  })
})

// 头像和名字点下去，和正文里点那个人的 @chip 一样：交给上一层去开他的成员页。
describe('点消息上的头像和名字', () => {
  it('头像和名字都通向说话的那个人', async () => {
    history = [aiMsg('a', '看过了')]
    const { container, emitted } = render(Panel, {
      props: { topic: topicOf('t1'), showComposer: true },
      global: { plugins: [vuetify, i18n] },
    })
    await settle()
    ;(container.querySelector('[data-mid="a"] .im-gutter button') as HTMLElement).click()
    ;(container.querySelector('[data-mid="a"] .im-name') as HTMLElement).click()
    expect(emitted()['mention-click']).toEqual([[SEAT], [SEAT]])
  })
})

// 任务里负责人可以把这件事单独交给另一位队友（任务信息卡那行「AI 队友」的「改」）。
// 换完之后发送框那个 @ 得跟着换：正文里写着「@芝士」而接手的是别人，读的人只会当成
// 换人没生效——和气泡上署错名是同一个报障，只是换到了输入框这一头。
describe('任务里单独指定了队友，发送框 @ 的是那一位', () => {
  const OTHER = 'cheese-t2'

  beforeEach(() => {
    sent.length = 0
    vi.stubGlobal('fetch', async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => {
        const u = String(url)
        if (u.includes('/members'))
          return {
            code: 200,
            data: {
              data: [
                { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
                { id: '2', member_handle: SEAT, name: '芝士', role: 'member', agent: true },
                { id: '3', member_handle: OTHER, name: '无言', role: 'member', agent: true },
              ],
              total: 3,
            },
          }
        if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
        if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
        return { code: 200, data: { data: history, total: history.length, has_more: false } }
      },
    }))
  })

  // 任务里每句话都说给做它的那位（线上 `always-summon` 就是这么传的）。
  function renderTask(room: string, taskId: string, handle: string | null) {
    return render(Panel, {
      props: {
        topic: topicOf(room),
        conversationId: taskId,
        taskAgentHandle: handle,
        alwaysSummon: true,
        showComposer: true,
      },
      global: { plugins: [vuetify, i18n] },
    })
  }

  // 名册是**异步**取的，名字就位之前发送框不补 @（RoomComposer 也是先等 summonReady）。
  // 先等提示语换成这件事那位再打字，否则测的是「名册还没到」那一刻，而不是换人有没有生效。
  async function typeAndSummon(container: Element, name: string) {
    const box = container.querySelector<HTMLTextAreaElement>('.composer textarea')
    expect(box).toBeTruthy()
    await waitFor(() => expect(box!.placeholder).toBe(t('work.room.composer.placeholderDm', { name })))
    box!.focus()
    await fireEvent.update(box!, '整理一下方案')
    await fireEvent.keyDown(box!, { key: 'Enter', ctrlKey: true })
    await settle()
    return box!
  }

  it('Ctrl+Enter 写进正文的 @ 是这件事那位，不是房间那位', async () => {
    history = []
    const { container } = renderTask('t1', 'task-1', OTHER)
    await settle()

    await typeAndSummon(container, '无言')
    expect(sent[0]?.content).toBe(`<@${OTHER}> 整理一下方案`)
  })

  it('这件事没单独指定，还是房间那位', async () => {
    history = []
    const { container } = renderTask('t2', 'task-2', null)
    await settle()

    await typeAndSummon(container, '芝士')
    expect(sent[0]?.content).toBe(`<@${SEAT}> 整理一下方案`)
  })
})
