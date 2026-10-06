/** 支线那几条规矩：输入框什么时候先带上「@芝士 」，主线上什么时候写「正在回复」。 */
import type { Block } from '../cx_types'

import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/threads', () => ({ listThreads: vi.fn() }))

import { summonPrefill, useThreadLines } from './useThreadLines'

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

  it('主线上叫了队友、还没有回复的那一条', () => {
    expect(lines(true).replyingFor(message(ME, true))).toBe('芝士')
  })

  it('支线已经有回复了，就写回复，不再写正在回复', () => {
    const asked = {
      ...message(ME, true),
      thread: {
        id: 't',
        room_id: 'room1',
        root_block_id: 'x',
        reply_count: 1,
        last_reply_at: null,
        last_reply: null,
      },
    }
    expect(lines(true).replyingFor(asked)).toBeNull()
  })

  it('没叫队友的消息、早就过去的那一条、不是主线的地方，都不写', () => {
    expect(lines(true).replyingFor(message(ME, false))).toBeNull()
    expect(lines(true).replyingFor(message(ME, true, '2026-01-01T00:00:00Z'))).toBeNull()
    expect(lines(false).replyingFor(message(ME, true))).toBeNull()
  })
})
