// 房间里在干活的队友做了哪一步，以及它多久没来新帧 —— 两条都从同一批 live 帧读，
// 换房间时一起作废。
import type { WsServerFrame } from '../../../cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useLiveSteps } from './useLiveSteps'

import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  vi.setSystemTime(Date.parse('2026-10-01T10:00:00Z'))
})

function live(agent: string, blocks: unknown[]): WsServerFrame {
  return { type: 'live', turn_id: 't1', agent, blocks } as unknown as WsServerFrame
}

describe('the step each member is on, from the room socket', () => {
  it('remembers the step a member is on, and when its frame arrived', () => {
    const { states, follow } = useLiveSteps()
    follow(live('cheese', [{ type: 'tool', id: 'c1', name: 'Bash', arguments: '{"command":"pnpm test"}' }]))
    expect(states.value.cheese).toEqual({ step: '执行命令 pnpm test', at: Date.now() })
  })

  it('clears the step once the frame says nothing is being written, but keeps the clock', () => {
    const { states, follow } = useLiveSteps()
    follow(live('cheese', [{ type: 'tool', id: 'c1', name: 'Bash', arguments: '{"command":"pnpm test"}' }]))
    vi.advanceTimersByTime(5_000)
    follow(live('cheese', []))
    expect(states.value.cheese).toEqual({ step: null, at: Date.now() })
  })

  it('keeps a step per member', () => {
    const { states, follow } = useLiveSteps()
    follow(live('cedar', [{ type: 'tool', id: 'c1', name: 'Read', arguments: '{"file_path":"a.ts"}' }]))
    follow(live('hazel', [{ type: 'tool', id: 'c2', name: 'Bash', arguments: '{"command":"ls"}' }]))
    expect(states.value.cedar.step).toBe('读取文件 a.ts')
    expect(states.value.hazel.step).toBe('执行命令 ls')
  })

  it('ignores frames that are not a live one', () => {
    const { states, follow } = useLiveSteps()
    follow({ type: 'turn_started', turn_id: 't1', agent: 'cheese' } as unknown as WsServerFrame)
    expect(states.value).toEqual({})
  })

  it('forgets everyone on reset', () => {
    const { states, follow, reset } = useLiveSteps()
    follow(live('cheese', [{ type: 'tool', id: 'c1', name: 'Bash', arguments: '{"command":"ls"}' }]))
    reset()
    expect(states.value).toEqual({})
  })
})
