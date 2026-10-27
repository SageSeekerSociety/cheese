import type { Task } from '@/types'

import { describe, expect, it } from 'vitest'

import { canStillAct, deadlineState, listDeadline, myClaimState, taskState } from './tasks'

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

  // 题目的状态对谁都一样（Zhifei 2026-10-26）；申请制的题截止的是申请，说「申请已截止」，
  // 不说一个分不清是哪个截止的「已截止」。
  it.each([
    [{ startMode: 'APPLY' as const, deadline: 500 }, 'applyClosed'],
    [{ startMode: 'DIRECT' as const, deadline: 500 }, 'closed'],
    [{ startMode: 'APPLY' as const, endedAt: 900 }, 'ended'],
    [{ startMode: 'APPLY' as const, deadline: 2000, submittable: true, userDeadline: 500 }, 'open'],
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

// 手上已经有一份领取的人，只看自己的提交截止：没到就能开始、接着做和交，两种开始方式都一样；
// 题目的截止（申请截止、直接开始的截止）只挡还没有领取的人（Zhifei 2026-10-26）。
describe('我手上那份领取还能不能动', () => {
  it.each([
    ['直接开始、题目截止过了、我的还没到', { startMode: 'DIRECT' as const, deadline: 500, userDeadline: 2000 }, true],
    ['申请制、申请截止过了、我的还没到', { startMode: 'APPLY' as const, deadline: 500, userDeadline: 2000 }, true],
    ['我的截止过了', { startMode: 'APPLY' as const, deadline: 2000, userDeadline: 500 }, false],
    ['题目结束了', { startMode: 'DIRECT' as const, endedAt: 900, userDeadline: 2000 }, false],
  ])('%s → %s', (_, overrides, can) => {
    expect(canStillAct(task({ submittable: true, ...overrides }), NOW)).toBe(can)
  })
})

describe('列表里我那一份的状态', () => {
  it.each([
    ['没领过', {}, null],
    ['申请了还在等', { joined: true, submittable: false }, 'applied'],
    ['通过了、还能做', { joined: true, submittable: true, userDeadline: 2000 }, 'doing'],
    ['通过了、自己的截止过了', { joined: true, submittable: true, userDeadline: 500 }, 'expired'],
    ['通过了、题目结束了', { joined: true, submittable: true, endedAt: 900 }, 'ended'],
    [
      '截止过了，但已经判了通过',
      { joined: true, submittable: true, userDeadline: 500, myClaimStatus: 'PASSED' as const },
      'passed',
    ],
    [
      '截止过了，交的那一版还在等批改',
      { joined: true, submittable: true, userDeadline: 500, myClaimStatus: 'SUBMITTED' as const },
      'submitted',
    ],
  ])('%s → %s', (_, overrides, key) => {
    expect(myClaimState(task(overrides), NOW)).toBe(key)
  })
})

// 列表里每道题只写一个日期，并且说清是哪一个截止（Zhifei 2026-10-26）。
describe('列表里那一个截止日期', () => {
  it.each([
    ['申请制、没有领取', { startMode: 'APPLY' as const, deadline: 100 }, { kind: 'apply', at: 100 }],
    [
      '申请制、在等通过',
      { startMode: 'APPLY' as const, deadline: 100, joined: true, submittable: false },
      { kind: 'apply', at: 100 },
    ],
    [
      '申请制、申请通过了：我自己的提交截止',
      { startMode: 'APPLY' as const, deadline: 100, joined: true, submittable: true, userDeadline: 900 },
      { kind: 'submit', at: 900 },
    ],
    ['直接开始、没有领取', { startMode: 'DIRECT' as const, deadline: 100 }, { kind: 'task', at: 100 }],
    [
      '直接开始、开始做了：我自己的提交截止',
      { startMode: 'DIRECT' as const, deadline: 100, joined: true, submittable: true, userDeadline: 900 },
      { kind: 'submit', at: 900 },
    ],
    ['申请制、不限截止', { startMode: 'APPLY' as const, deadline: null }, { kind: 'apply', at: null }],
  ])('%s', (_, overrides, expected) => {
    expect(listDeadline(task(overrides))).toEqual(expected)
  })
})
