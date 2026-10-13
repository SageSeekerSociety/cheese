import type { Task } from '@/types'

import { describe, expect, it } from 'vitest'

import { deadlineState, taskState } from './tasks'

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

describe('一个截止时刻此刻是什么情形', () => {
  // 本地时间造出来的时刻：不管测试机在哪个时区，「今天」都是 10 月 10 日。
  const now = new Date(2030, 9, 10, 18, 0).getTime()
  const at = (day: number, hour: number) => new Date(2030, 9, day, hour, 0).getTime()

  it.each([
    ['今天早些时候', at(10, 9), { passed: true, days: 0 }],
    ['昨天晚上，离现在不到 24 小时', at(9, 21), { passed: true, days: 1 }],
    ['三天前', at(7, 12), { passed: true, days: 3 }],
    ['今天晚些时候', at(10, 23), { passed: false, days: 0 }],
    ['明天一早，离现在不到 24 小时', at(11, 8), { passed: false, days: 1 }],
    ['五天后', at(15, 12), { passed: false, days: 5 }],
  ])('%s', (_, deadline, expected) => {
    expect(deadlineState(deadline, now)).toEqual(expected)
  })

  it('正好到点还不算过，和服务端「截止之后不收」同一条判据', () => {
    expect(deadlineState(now, now)?.passed).toBe(false)
    expect(deadlineState(now - 1, now)?.passed).toBe(true)
  })

  it('没有截止就没有情形', () => {
    expect(deadlineState(null, now)).toBeNull()
  })
})
