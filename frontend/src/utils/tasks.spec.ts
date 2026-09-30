import type { Task } from '@/types'

import { describe, expect, it } from 'vitest'

import { taskState } from './tasks'

const NOW = 1000

function task(overrides: Partial<Task> = {}) {
  return {
    approved: 'APPROVED',
    deadline: null,
    registrationStartAt: undefined,
    participantLimit: 0,
    participants: { total: 0, examples: [] },
    ...overrides,
  } as Task
}

describe('题目此刻的状态', () => {
  it.each([
    [{}, 'open'],
    [{ approved: 'NONE' as const }, 'pending'],
    [{ approved: 'DISAPPROVED' as const }, 'rejected'],
    [{ deadline: 500 }, 'closed'],
    [{ deadline: 2000 }, 'open'],
    [{ registrationStartAt: 2000 }, 'notStarted'],
    [{ participantLimit: 3, participants: { total: 3, examples: [] } }, 'full'],
    [{ participantLimit: 3, participants: { total: 2, examples: [] } }, 'open'],
  ])('%o → %s', (overrides, key) => {
    expect(taskState(task(overrides), NOW).key).toBe(key)
  })

  it('人数上限 0 是不限，不是满员', () => {
    expect(taskState(task({ participantLimit: 0, participants: { total: 50, examples: [] } }), NOW).key).toBe('open')
  })
})
