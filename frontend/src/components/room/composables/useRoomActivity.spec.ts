// 「我在打字」那一下带着这条草稿会交给哪位 AI 队友：后端靠它趁人还在打字先把那位
// 队友的会话启动好。
import type { WsClientMessage } from '../../../cx_types'

import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TYPING_PING_MS, useRoomActivity } from './useRoomActivity'

function activity() {
  const sent: WsClientMessage[] = []
  const scope = effectScope()
  const room = scope.run(() => useRoomActivity({ me: 'alice', send: (m) => sent.push(m) }))!
  return { room, sent, stop: () => scope.stop() }
}

describe('我在打字时告诉房间这条草稿交给谁', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('草稿谁也不交给时，只说我在打字', () => {
    const { room, sent, stop } = activity()
    room.composing('大家好')
    expect(sent).toEqual([{ type: 'typing' }])
    stop()
  })

  it('草稿交给某位队友时，打字的那一下带上它和所在的对话', () => {
    const { room, sent, stop } = activity()
    room.addressing({ agent: 'cheese', place: 'task-1' })
    expect(sent).toEqual([])
    room.composing('帮我看一下')
    expect(sent).toEqual([{ type: 'typing', agent: 'cheese', place: 'task-1' }])
    stop()
  })

  it('打着字时才 @ 到队友，立刻说一次，不等下一个三秒', () => {
    const { room, sent, stop } = activity()
    room.composing('@')
    room.addressing({ agent: 'cheese', place: 'room-1' })
    expect(sent).toEqual([{ type: 'typing' }, { type: 'typing', agent: 'cheese', place: 'room-1' }])
    // 同一个回答再来一次不重复说；三秒之内接着打字也不说。
    room.addressing({ agent: 'cheese', place: 'room-1' })
    room.composing('@芝士 你好')
    expect(sent).toHaveLength(2)
    vi.advanceTimersByTime(TYPING_PING_MS)
    room.composing('@芝士 你好呀')
    expect(sent[2]).toEqual({ type: 'typing', agent: 'cheese', place: 'room-1' })
    stop()
  })

  it('删掉了 @，之后打字不再带那位队友', () => {
    const { room, sent, stop } = activity()
    room.addressing({ agent: 'cheese', place: 'room-1' })
    room.addressing(null)
    room.composing('算了')
    expect(sent).toEqual([{ type: 'typing' }])
    stop()
  })
})
