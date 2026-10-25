// 回答里自动化卡片的状态从哪来：读回来的列表；取消了的说「已取消」，哪怕列表里已经没有它；
// 一轮回答结束后重读（这一轮可能刚建了一条）。
import type { AssistantRoutine } from '@/api'

import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', () => ({
  getAssistantRoutines: vi.fn(),
  setAssistantRoutine: vi.fn(),
}))

import { useAgentRules } from './useAgentRules'

import { getAssistantRoutines, setAssistantRoutine } from '@/api'

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

function rule(id: string, fields: Partial<AssistantRoutine> = {}): AssistantRoutine {
  return {
    id,
    title: id,
    note: '',
    when: '',
    state: 'active',
    paused: '',
    nextRunAt: null,
    once: true,
    conversationId: 'c',
    lastRun: null,
    weekCredits: 0,
    ...fields,
  }
}

const listed = (routines: AssistantRoutine[]) => ({ routines, limit: 20 })

describe('useAgentRules', () => {
  beforeEach(() => vi.resetAllMocks())

  it('says how each rule stands, from the rules as read', async () => {
    vi.mocked(getAssistantRoutines).mockResolvedValue(
      listed([
        rule('waiting'),
        rule('on', { once: false }),
        rule('ran', { state: 'done', lastRun: { status: 'done', at: '', asked: 0, conversationId: 'c' } }),
        rule('gave-up', { state: 'done' }),
      ])
    )
    const rules = useAgentRules(ref('c'), ref(false))
    expect(rules.now('waiting')).toBeNull()
    await settle()

    expect(rules.now('waiting')).toEqual({ state: 'waiting' })
    expect(rules.now('on')).toEqual({ state: 'on' })
    expect(rules.now('ran')).toEqual({ state: 'started' })
    expect(rules.now('gave-up')).toEqual({ state: 'gaveUp' })
    expect(rules.now('gone')).toEqual({ state: 'ended' })
  })

  it('a rule cancelled here says cancelled, not merely gone', async () => {
    vi.mocked(getAssistantRoutines).mockResolvedValue(listed([rule('w')]))
    vi.mocked(setAssistantRoutine).mockResolvedValue(listed([]))
    const rules = useAgentRules(ref('c'), ref(false))
    await settle()

    expect(await rules.act('w', 'cancel')).toBe(true)
    expect(rules.now('w')).toEqual({ state: 'cancelled' })
  })

  it('reads again once an answer ends, which may have made one', async () => {
    vi.mocked(getAssistantRoutines).mockResolvedValueOnce(listed([]))
    const busy = ref(true)
    const rules = useAgentRules(ref('c'), busy)
    await settle()
    vi.mocked(getAssistantRoutines).mockResolvedValueOnce(listed([rule('new')]))

    busy.value = false
    await settle()

    expect(rules.now('new')).toEqual({ state: 'waiting' })
  })
})
