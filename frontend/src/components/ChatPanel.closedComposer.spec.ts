// 不能说话的地方（任务里不是负责人、任务已关闭）：输入框的位置换成一句为什么，没有
// 任何能打字或发送的东西；能说话时输入框照旧。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const Panel = ChatPanel as unknown as Component

const room = {
  id: 'r1',
  project_id: 'p1',
  parent_id: null,
  title: '前端',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-10-05T00:00:00Z',
  updated_at: '2026-10-05T00:00:00Z',
} as Topic

const requested: string[] = []

beforeEach(() => {
  setLocale('zh-CN')
  requested.length = 0
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => {
    requested.push(String(url))
    return {
      ok: true,
      status: 200,
      json: async () =>
        String(url).includes('/progress')
          ? { code: 200, data: { items: [], updated_at: null } }
          : { code: 200, data: { data: [], total: 0 } },
    }
  })
})

function mount(props: Record<string, unknown>) {
  return render(Panel, {
    props: { topic: room, showComposer: true, ...props },
    slots: { 'composer-closed': '<button class="back">回到房间</button>' },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  }).container
}

describe('不能说话的地方', () => {
  it('输入框的位置写着为什么，没有能打字的地方', () => {
    const c = mount({ conversationId: 't1', composerClosed: '只有负责人可以在这里和芝士对话' })
    expect(c.textContent).toContain('只有负责人可以在这里和芝士对话')
    expect(c.querySelector('textarea')).toBeNull()
    expect(c.querySelector('.composer-send')).toBeNull()
    expect(c.querySelector('.back')).not.toBeNull()
  })

  it('能说话时输入框照旧', () => {
    const c = mount({ conversationId: 't1' })
    expect(c.querySelector('textarea')).not.toBeNull()
  })

  it('读的是任务自己的对话，不是房间的', async () => {
    mount({ conversationId: 't1' })
    await vi.waitFor(() => expect(requested.some((u) => u.includes('/topics/t1/blocks'))).toBe(true))
    expect(requested.some((u) => u.includes('/topics/r1/blocks'))).toBe(false)
  })
})
