/** 支线那几条规矩：输入框什么时候先带上「@芝士 」，主线上什么时候写「正在回复」。 */
import type { Block } from '../cx_types'

import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/threads', () => ({ listThreads: vi.fn(), getThread: vi.fn() }))

import { summonPrefill, useThreadLines } from './useThreadLines'

import { listThreads } from '@/api/threads'
import { setLocale } from '@/i18n'

const ME = 'alice'

function message(author: string, called: boolean, at = new Date().toISOString()): Block {
  return {
    id: `${author}-${Math.random()}`,
    conversation_id: 'thread1',
    kind: 'message',
    author_type: 'participant',
    author,
    content: '…',
    meta: called ? { agent_recipient: { handle: 'cheese', mentioned: true } } : {},
    created_at: at,
  }
}
const mine = (m: Block) => m.author === ME

describe('支线输入框先带上「@芝士 」', () => {
  it('我在这条支线里的上一句叫过它', () => {
    const said = [message(ME, true), message('bob', false)]
    expect(summonPrefill(said, mine, '芝士')).toBe('@芝士 ')
  })

  it('我上一句没叫它，哪怕更早叫过', () => {
    const said = [message(ME, true), message(ME, false)]
    expect(summonPrefill(said, mine, '芝士')).toBe('')
  })

  it('我还没在这里说过话', () => {
    expect(summonPrefill([message('bob', true)], mine, '芝士')).toBe('')
  })
})

describe('主线上「正在回复」', () => {
  function lines(mainLine: boolean) {
    return useThreadLines({
      mainLine: () => mainLine,
      roomId: () => 'room1',
      timeline: { messages: ref([]), find: () => undefined, replace: () => {} },
      agentNameOf: () => '芝士',
    })
  }
  function underThread(replying: string[], replies = 0, at?: string): Block {
    return {
      ...message(ME, true, at),
      thread: {
        id: 't1',
        room_id: 'room1',
        root_block_id: 'x',
        reply_count: replies,
        last_reply_at: null,
        last_reply: null,
        participants: [ME],
        task: null,
        replying,
      },
    }
  }

  it('队友此刻在支线里答，就写它正在回复，支线里已经有回复也写', () => {
    expect(lines(true).replyingFor(underThread(['cheese'], 3))).toBe('芝士')
  })

  it('叫过它不算：它此刻没在答就不写，不管叫了多久', () => {
    expect(lines(true).replyingFor(underThread([], 0))).toBeNull()
    expect(lines(true).replyingFor(message(ME, true))).toBeNull()
  })

  it('它开始、停下回答时，那一行跟着变', async () => {
    const l = lines(true)
    const asked = underThread([], 1)
    await l.onActivity('t1', 'cheese', true)
    expect(l.replyingFor(asked)).toBe('芝士')
    await l.onActivity('t1', 'cheese', false)
    expect(l.replyingFor(asked)).toBeNull()
  })

  it('不是频道主线的地方不写', () => {
    expect(lines(false).replyingFor(underThread(['cheese']))).toBeNull()
  })
})

describe('主线上写队友在支线里等什么', () => {
  setLocale('zh-CN')
  const asked = (): Block => ({
    ...message(ME, true),
    thread: {
      id: 't1',
      room_id: 'room1',
      root_block_id: 'x',
      reply_count: 0,
      last_reply_at: null,
      last_reply: null,
      participants: [ME],
      task: null,
    },
  })
  function record(eventType: string, meta: Record<string, unknown> = {}): Block {
    return {
      id: `r-${eventType}`,
      conversation_id: 't1',
      kind: 'event',
      author_type: 'platform',
      author: 'system',
      content: '',
      turn_id: 'turn1',
      created_at: new Date().toISOString(),
      meta: { event_type: eventType, seat: 'cheese', ...meta },
    }
  }
  const lines = () =>
    useThreadLines({
      mainLine: () => true,
      roomId: () => 'room1',
      timeline: { messages: ref([]), find: () => undefined, replace: () => {} },
      agentNameOf: () => '芝士',
    })

  it('还没开始、在排队：那一行写它在排队、前面还有几个', () => {
    const l = lines()
    l.onStatus('t1', record('turn_queued', { i18n: { content: { params: { ahead: 2 } } } }))
    expect(l.replyingFor(asked())).toBe('芝士')
    expect(l.statusFor(asked())).toContain('2')
  })

  it('开始回答了就不再说排队；中途重试写第几次；停下来什么都不写', async () => {
    const l = lines()
    l.onStatus('t1', record('turn_queued'))
    await l.onActivity('t1', 'cheese', true)
    expect(l.statusFor(asked())).toBeNull()
    l.onStatus('t1', record('api_retry', { attempt: 3 }))
    expect(l.statusFor(asked())).toContain('3')
    await l.onActivity('t1', 'cheese', false)
    expect(l.statusFor(asked())).toBeNull()
    expect(l.replyingFor(asked())).toBeNull()
  })

  it('说不出在等什么的记录（记忆改了）不改那一行', () => {
    const l = lines()
    l.onStatus('t1', record('memory_changed'))
    expect(l.statusFor(asked())).toBeNull()
  })
})

describe('一轮出错、还没有回复的支线', () => {
  it('队友停下时支线里还没有回复：再读一次，那条消息下面那一行就说回复失败', async () => {
    const asked: Block = {
      ...message(ME, true),
      id: 'root',
      thread: {
        id: 't1',
        room_id: 'room1',
        root_block_id: 'root',
        reply_count: 0,
        last_reply_at: null,
        last_reply: null,
        participants: [ME],
        task: null,
      },
    }
    const messages = ref<Block[]>([asked])
    vi.mocked(listThreads).mockResolvedValue([{ ...asked.thread!, failed: true, root: null, unread: false }])
    const l = useThreadLines({
      mainLine: () => true,
      roomId: () => 'room1',
      timeline: {
        messages,
        find: (id) => messages.value.find((m) => m.id === id),
        replace: (b) => (messages.value = messages.value.map((m) => (m.id === b.id ? b : m))),
      },
      agentNameOf: () => '芝士',
    })

    await l.onActivity('t1', 'cheese', true)
    await l.onActivity('t1', 'cheese', false)
    await vi.waitFor(() => expect(messages.value[0].thread?.failed).toBe(true))
    expect(l.replyingFor(messages.value[0])).toBeNull()
  })
})
