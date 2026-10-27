import type { Task } from '@/types'

import { describe, expect, it } from 'vitest'

import { deadlineState, taskState, taskStateFor } from './tasks'

const NOW = 1000

function task(overrides: Partial<Task> = {}) {
  return {
    approved: 'APPROVED',
    deadline: null,
    registrationStartAt: undefined,
    participantLimit: 0,
    participants: { total: 0, approved: 0, examples: [] },
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
    [{ participantLimit: 3, participants: { total: 3, approved: 3, examples: [] } }, 'full'],
    // 等批的、被拒的申请不占名额：5 份申请里只通过了 2 份，3 个名额就还没满。
    [{ participantLimit: 3, participants: { total: 5, approved: 2, examples: [] } }, 'open'],
  ])('%o → %s', (overrides, key) => {
    expect(taskState(task(overrides), NOW).key).toBe(key)
  })

  it('开放着的题按开始方式说：直接开始「可开始」，申请制「可申请」', () => {
    expect(taskState(task({ startMode: 'DIRECT' }), NOW).key).toBe('openToStart')
    expect(taskState(task({ startMode: 'APPLY' }), NOW).key).toBe('open')
  })

  it('人数上限 0 是不限，不是满员', () => {
    expect(
      taskState(task({ participantLimit: 0, participants: { total: 50, approved: 50, examples: [] } }), NOW).key
    ).toBe('open')
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

describe('这道题对手上有一份通过了的领取的人是什么状态', () => {
  const mine = (overrides: Partial<Task>) => task({ submittable: true, startMode: 'APPLY', ...overrides })

  it.each([
    // 过的是我自己的截止：「已过期」，题目还收不收申请都一样。
    [{ userDeadline: 500, deadline: 2000 }, 'expired'],
    [{ userDeadline: 500, deadline: 400 }, 'expired'],
    // 过的是题目：结束了，或直接开始的题过了所有人的截止。
    [{ userDeadline: 2000, endedAt: 900 }, 'closed'],
    [{ userDeadline: 2000, startMode: 'DIRECT' as const, deadline: 500 }, 'closed'],
    // 申请截止只挡申请，我的截止还没到：不写「已截止」。
    [{ userDeadline: 2000, deadline: 500 }, null],
    // 还在收申请、我已经通过了：申请这一步与我无关，不写「可申请」。
    [{ userDeadline: 2000, deadline: 2000 }, null],
  ])('%o → %s', (overrides, key) => {
    expect(taskStateFor(mine(overrides), NOW)?.key ?? null).toBe(key)
  })
})
