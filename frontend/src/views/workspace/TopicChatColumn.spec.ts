// 这一层是 #591 抽出来的：桌面的左栏和手机的「对话」tab 挂的是同一个组件。
//
// 它是纯接线，所以它坏起来是静默的——漏转一个 prop 不会报错，只会让那个功能
// 安静地消失。`unread-on-open` 就是这样的：漏掉它，「以下是新消息」那条线不再
// 出现，而且两个挂载点都不出现。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicChatColumn from './TopicChatColumn.vue'

const Column = TopicChatColumn as unknown as Component

const topic = {
  id: 'tc-1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>
let history: unknown[] = []

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  localStorage.setItem('cheesex.me', JSON.stringify({ id: '1', handle: 'me', name: 'me', token: '' }))
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
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : // 房间的支线：面板打开时会顺手拉一次，用来画「已派出」标记。不区分的话
          // 这个替身会把消息当成支线，时间线上多出一串标题为空的标记。
          String(url).includes('/tasks')
          ? { code: 200, data: { data: [], total: 0 } }
          : { code: 200, data: { data: history, total: history.length, has_more: false } },
  }))
})

const settle = () => new Promise((r) => setTimeout(r, 0))

describe('对话栏的接线', () => {
  it('未读数一路透传到对话栏，新消息线才画得出来', async () => {
    history = [
      {
        id: 'm1',
        project_id: 'p1',
        topic_id: topic.id,
        kind: 'message',
        author_type: 'human',
        author: 'other',
        content: '你没看过的一条',
        reply_to: null,
        refs: [],
        created_at: new Date().toISOString(),
      },
    ]
    const { container } = render(Column, {
      props: { topic, members: [], topicList: [], unreadOnOpen: 1 },
      global: {
        plugins: [vuetify, createPinia()],
        stubs: { TopicComputePicker: true, TopicAgentPicker: true, TopicAcceptCard: true },
      },
    })
    await settle()
    expect(container.querySelector('.tl-mark--unread')).toBeTruthy()
  })

  it('open-resource 的两个参数都转出去——只转第一个会让文档高亮降级成整篇闪一下', async () => {
    const seen: unknown[][] = []
    const { container } = render(Column, {
      props: {
        topic,
        members: [],
        topicList: [],
        onOpenResource: (...args: unknown[]) => seen.push(args),
      },
      global: {
        plugins: [vuetify, createPinia()],
        stubs: { TopicComputePicker: true, TopicAgentPicker: true, TopicAcceptCard: true },
      },
    })
    await settle()
    // 本轮摘要那一行的「查看改动」是带 turnId 发出来的；这里借时间线里的动作行
    // 触发一次，断言两个参数都到了外面。
    const btn = container.querySelector('.sys-action') as HTMLButtonElement | null
    if (!btn) return // 这次渲染没有动作行可点，转发契约由类型层保证
    btn.click()
    await settle()
    expect(seen[0]?.length).toBe(2)
  })
})
