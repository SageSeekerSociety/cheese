// AI 队友头像的表情跟着它此刻的状态走。这里守的规则：
//   * 一轮在跑：还没任何动静是在想，一步正在做是在干活，平台在重试是卡住了，在等
//     工作电脑是等机器；
//   * 几位队友并行在干，各是各的表情；同一位并行两轮，看最近开始的那一轮；
//   * 一轮刚结束：做完了；以失败收场就是卡住了——失败那一行先到后到都一样；
//   * 没在干活的队友没有表情。
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { agentFaces } from './agentFace'

let seq = 0
function event(turn: string, meta: Record<string, unknown>): Block {
  seq += 1
  return {
    id: `e${seq}`,
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '',
    turn_id: turn,
    created_at: new Date(Date.UTC(2026, 9, 1, 10, 0, seq)).toISOString(),
    meta,
  } as Block
}

const A = 'cheese-a1'
const B = 'cheese-b2'

function faceOf(blocks: Block[], handle = A) {
  return agentFaces(blocks, { t1: 1000 }, { t1: handle })[handle]?.state ?? null
}

describe('agentFaces', () => {
  it('一轮刚开始、还没有任何动静：在想', () => {
    expect(faceOf([])).toBe('think')
  })

  it('一步正在做：在干活；这一步交回了结果、又没开始下一步：回到在想', () => {
    const step = event('t1', { tool: 'Bash' })
    expect(faceOf([step])).toBe('work')
    expect(faceOf([{ ...step, meta: { tool: 'Bash', output_bytes: 12 } }])).toBe('think')
  })

  it('平台在重试：卡住了', () => {
    expect(faceOf([event('t1', { event_type: 'api_retry', attempt: 2 })])).toBe('stuck')
  })

  it('还在等工作电脑：等机器', () => {
    expect(faceOf([event('t0', { event_type: 'cloud_provisioning', state: 'waiting' })])).toBe('wait')
  })

  it('几位队友并行在干，各是各的表情', () => {
    const faces = agentFaces([event('t2', { tool: 'Read' })], { t1: 1000, t2: 2000 }, { t1: A, t2: B })
    expect(faces[A]?.state).toBe('think')
    expect(faces[B]?.state).toBe('work')
  })

  it('同一位队友并行两轮：看最近开始的那一轮', () => {
    const faces = agentFaces([event('old', { tool: 'Read' })], { old: 1000, new: 2000 }, { old: A, new: A })
    expect(faces[A]?.state).toBe('think')
  })

  it('一轮刚结束：做完了；以失败收场是卡住了', () => {
    expect(agentFaces([], {}, {}, { [A]: 't1' })[A]?.state).toBe('done')
    const failed = [event('t1', { event_type: 'turn_failed' })]
    expect(agentFaces(failed, {}, {}, { [A]: 't1' })[A]?.state).toBe('stuck')
  })

  it('刚结束之后又开了一轮：跟着新的这一轮走', () => {
    expect(agentFaces([], { t2: 2000 }, { t2: A }, { [A]: 't1' })[A]?.state).toBe('think')
  })

  it('没在干活的队友没有表情', () => {
    expect(agentFaces([event('t1', { tool: 'Bash' })], { t1: 1000 }, { t1: A })[B]).toBeUndefined()
  })
})
