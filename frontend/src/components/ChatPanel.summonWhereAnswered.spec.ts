// 叫芝士的那条消息在哪儿被回答，「芝士在这儿干活」就只在哪儿亮。
//
// 频道主线上 @ 它，它在那条消息底下的支线里回答：那一轮的开工、收工只发给支线，
// 主线听不到（主线上那条消息底下的一行说谁在回答）。主线要是在发出去的那一刻就
// 自己亮起「在干活」，就没有任何一帧会把它关掉：回答早就落进支线了，主线还一直说
// 它在跑 —— 跑过一分钟的提示（「这次运行时间可能较长」）就是这样在回答之后冒出来的。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listProjectLibrary: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listBlocks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listTopicMembers: vi.fn().mockResolvedValue({
      data: [
        { member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
        { member_handle: 'cheese-room', name: '芝士', role: 'member', agent: true },
      ],
      total: 2,
    }),
    chatWsUrl: () => 'ws://test/ws',
  }
})

vi.mock('@/api/messages', () => ({
  postChatMessage: vi.fn(async (conversationId: string, body: { content: string; request_id: string }) => ({
    id: body.request_id,
    conversation_id: conversationId,
    kind: 'message',
    author_type: 'participant',
    author: 'alice',
    content: body.content,
    meta: { client_id: body.request_id },
    created_at: new Date().toISOString(),
  })),
}))

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component

function channel(id: string, extra: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: '综合',
    kind: 'topic',
    status: 'active',
    created_at: '2026-10-07T00:00:00Z',
    ...extra,
  } as Topic
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeAll(() => {
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 1
      onopen: (() => void) | null = null
      onclose: (() => void) | null = null
      onerror: (() => void) | null = null
      onmessage: (() => void) | null = null
      close() {}
      send() {}
    }
  )
})

async function summonFrom(props: Record<string, unknown>) {
  const view = render(Panel, {
    props: { showComposer: true, hideHeader: true, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await flush()
  const box = view.container.querySelector<HTMLTextAreaElement>('.composer textarea')!
  box.focus()
  await fireEvent.update(box, '@芝士 看看这个')
  await fireEvent.keyDown(box, { key: 'Enter' })
  await flush()
  return view
}

function saidWorking(view: Awaited<ReturnType<typeof summonFrom>>): boolean {
  return (view.emitted('working') ?? []).some((args) => (args as unknown[])[0] === true)
}

describe('叫芝士之后，哪里说它在干活', () => {
  it('频道主线上叫它，主线不说它在干活 —— 它在支线里回答', async () => {
    const view = await summonFrom({ topic: channel('main-line-summon') })

    expect(saidWorking(view)).toBe(false)
  })

  it('在支线或任务里叫它，那里马上说它在干活', async () => {
    const view = await summonFrom({ topic: channel('room-of-a-task'), conversationId: 'task-conversation' })

    expect(saidWorking(view)).toBe(true)
  })

  it('私聊里叫它，私聊里马上说它在干活 —— 私聊没有支线', async () => {
    const view = await summonFrom({ topic: channel('a-private-chat'), noUpgrade: true })

    expect(saidWorking(view)).toBe(true)
  })

  it('已归档的频道不开支线：在那里叫它，回答（被拒）也在那里', async () => {
    const view = await summonFrom({ topic: channel('archived-channel', { status: 'archived' }) })

    expect(saidWorking(view)).toBe(true)
  })
})
