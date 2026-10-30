// 发出去之后的两件事：
// - 只有自己一个人、这条又没交给 AI 队友：自己那条下面说一声，并给出「回复并 @ 它」那条路；
//   有别人在的频道里不说（频道默认是人和人说话）。
// - 发的时候看到的是「交给」队友，送到时已经不交给了（打字时任务关了）：这条不发，原话回到
//   输入框，就地问交给谁，不显示成交给了队友的一条。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'
import { composerText, fillComposer } from '@/test/composerInput'
import { loadMarkdownReader } from '@/test/markdownReader'

const Panel = ChatPanel as unknown as Component
const SEAT = 'cheese-t1'
let vuetify: ReturnType<typeof createVuetify>

const server = {
  /** 输入框问「交给谁」时后端的回答。 */
  addressing: { agent: null, reason: null, people: [], place: 'channel', task_status: null } as Record<string, unknown>,
  /** 发出去那一条，后端是落库（并说没别人在），还是因为收件人变了退回来。 */
  answer: 'posted' as 'posted' | 'alone' | 'readdressed',
  sent: [] as Record<string, unknown>[],
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}

function stored(body: Record<string, unknown>): Block {
  return {
    id: String(body.request_id),
    project_id: 'p1',
    conversation_id: 't1',
    turn_id: String(body.request_id),
    kind: 'message',
    author_type: 'participant',
    author: 'me',
    content: String(body.content),
    reply_to: null,
    refs: [],
    meta: { client_id: body.request_id },
    created_at: new Date().toISOString(),
  } as unknown as Block
}

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})
beforeAll(loadMarkdownReader)

beforeEach(() => {
  setLocale('zh-CN')
  server.addressing = { agent: null, reason: null, people: [], place: 'channel', task_status: null }
  server.answer = 'posted'
  server.sent = []
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 1
      onopen: (() => void) | null = null
      constructor() {
        setTimeout(() => this.onopen?.(), 0)
      }
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url)
    if (u.endsWith('/messages/addressing')) return json({ code: 200, data: server.addressing })
    if (u.endsWith('/messages') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      server.sent.push(body)
      if (server.answer === 'readdressed')
        return json(
          {
            code: 409,
            message: '这条消息现在不会交给 AI 队友，没有发出',
            error: {
              name: 'AddressingChanged',
              message: '这条消息现在不会交给 AI 队友，没有发出',
              data: { agent: null, reason: null, people: [], place: 'task', task_status: 'closed' },
            },
          },
          409
        )
      return json({ code: 200, data: { ...stored(body), ...(server.answer === 'alone' ? { alone: true } : {}) } })
    }
    if (u.includes('/members'))
      return json({
        code: 200,
        data: {
          data: [
            { id: '1', member_handle: 'me', name: '我', role: 'owner', agent: false },
            { id: '2', member_handle: SEAT, name: '芝士', role: 'member', agent: true },
          ],
          total: 2,
        },
      })
    if (u.includes('/progress')) return json({ code: 200, data: { items: [], updated_at: null } })
    if (u.includes('/tasks')) return json({ code: 200, data: { data: [], total: 0 } })
    return json({ code: 200, data: { data: [], total: 0, has_more: false } })
  })
})

// 打字停下之后输入框才问后端「交给谁」（useAddressing 等 150ms），再等它答完。
const ANSWERED_MS = 200

async function settle() {
  await new Promise((r) => setTimeout(r, ANSWERED_MS))
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function openAndSend(text: string) {
  const view = render(Panel, {
    props: { topic: { id: 't1', project_id: 'p1', title: '综合', kind: 'root' } as Topic, showComposer: true },
    global: { plugins: [vuetify, i18n] },
  })
  await settle()
  const box = view.container.querySelector<HTMLElement>('.ProseMirror')!
  box.focus()
  await fillComposer(box, text)
  await settle()
  await fireEvent.keyDown(box, { key: 'Enter' })
  await settle()
  return { ...view, box }
}

const RECEIPT = '已发布。这里目前只有你；本条未请求芝士处理。'

describe('只有自己一个人时，没交给谁的一条', () => {
  it('自己那条下面说一声；「回复并 @芝士」回复这一条并 @ 上它', async () => {
    server.answer = 'alone'
    const view = await openAndSend('帮我列个下周的计划')

    expect(view.container.textContent).toContain(RECEIPT)
    await fireEvent.click(view.getByRole('button', { name: '回复并 @芝士' }))
    await settle()

    expect(composerText(view.box)).toContain(`<@${SEAT}>`)
    expect(view.container.querySelector('.composer')!.textContent).toContain('帮我列个下周的计划')
    view.unmount()
  })

  it('有别人在的频道里不说', async () => {
    server.answer = 'posted'
    const view = await openAndSend('明天几点开会？')

    expect(server.sent).toHaveLength(1)
    expect(view.container.textContent).not.toContain(RECEIPT)
    view.unmount()
  })
})

describe('送到时已经不交给队友了', () => {
  it('这条不发：原话回到输入框，就地问交给谁', async () => {
    server.addressing = {
      agent: { handle: SEAT, name: '芝士' },
      reason: 'task',
      people: [],
      place: 'task',
      task_status: 'open',
    }
    server.answer = 'readdressed'
    const view = await openAndSend('再加一个导出接口')

    expect(server.sent[0]).toMatchObject({ content: '再加一个导出接口', expects_agent: true })
    expect(composerText(view.box)).toBe('再加一个导出接口')
    expect(view.container.textContent).toContain('任务已关闭，这条不会再自动交给芝士')
    // 时间线上没有一条「发送失败」或冒充交给了队友的那条：它整条回到了输入框。
    expect(view.container.querySelector('.outbox-fail')).toBeNull()
    expect(view.container.querySelector('.timeline-zone')?.textContent ?? '').not.toContain('再加一个导出接口')
    view.unmount()
  })
})
