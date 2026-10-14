// 我在频道里 @ 了不在里面的人：消息发出去之后，输入框上方问我要不要把她加进来（Slack
// 那样），只有我看得见；点「添加到频道」就走频道名册加人的那条路，加的是这个频道。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale, t } from '@/i18n'

vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listBlocks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    addTopicMember: (room: string, handle: string) => addTopicMember(room, handle),
    listTopicMembers: vi.fn().mockResolvedValue({
      data: [
        { member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
        { member_handle: 'cheese-room', name: '芝士', role: 'member', agent: true },
      ],
      total: 2,
    }),
  }
})

const addTopicMember = vi.fn(async (_room: string, handle: string) => ({ member_handle: handle }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

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
    // 后端回这条消息时说：@ 到的 carol 不在这个私密频道里，发的人能加她。
    mentioned_outside: { room_id: 'secret', handles: ['carol'], may_add: true, members_only: true },
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

describe('@ 了不在频道里的人', () => {
  it('发出去之后问要不要加进来，加的是这个频道', async () => {
    const view = render(Panel, {
      props: {
        showComposer: true,
        hideHeader: true,
        topic: channel('secret', { title: '机密频道' }),
        members: [{ user_handle: 'carol', name: '张衡' }],
      },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })
    await flush()
    const box = view.container.querySelector<HTMLTextAreaElement>('.composer textarea')!
    box.focus()
    await fireEvent.update(box, '<@carol> 看一下')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await flush()

    const add = screen.getByRole('button', { name: t('work.room.mentionOutside.add') })
    expect(add.closest('[role="status"]')?.textContent).toContain('张衡')
    await fireEvent.click(add)
    await flush()

    expect(addTopicMember).toHaveBeenCalledWith('secret', 'carol')
  })
})
