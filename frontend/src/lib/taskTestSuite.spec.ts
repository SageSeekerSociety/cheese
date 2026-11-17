// 发题表单写出去的测试点：只发有测试点那一行的阶段那一套，阶段写成整理过的 id；缺运行
// 命令或用例时拦住；阶段变了跟着挪。
import type { TaskRubricLine, TaskStage } from '@/types'

import { describe, expect, it } from 'vitest'

import { incompleteSuite, newTestSuite, suitesAfterStageChange, suitesToSend } from './taskTestSuite'

const stages: TaskStage[] = [
  { id: 'a1', name: '一', description: '', dueAt: null },
  { id: 'b2', name: '二', description: '', dueAt: null },
]
const tests = (stage: string | null): TaskRubricLine => ({ item: '正确性', points: 60, criteria: '', stage, kind: 'tests' })

describe('suitesToSend', () => {
  it('只发有测试点那一行的阶段的那一套，读回来才有的几项去掉', () => {
    const kept = { ...newTestSuite('a1'), revision: 3, caseCount: 1, totalPoints: 10 }
    const dropped = newTestSuite('b2')
    const sent = suitesToSend([kept, dropped], [tests('a1')], stages)
    expect(sent).toHaveLength(1)
    expect(sent[0].stage).toBe('a1')
    expect(sent[0]).not.toHaveProperty('revision')
    expect(sent[0]).not.toHaveProperty('caseCount')
  })

  it('指着已经没有的阶段的那一套归第一个阶段（和评分要点同一条规矩）', () => {
    const sent = suitesToSend([newTestSuite('gone')], [tests('a1')], stages)
    expect(sent.map((s) => s.stage)).toEqual(['a1'])
  })

  it('同一个阶段只发一套', () => {
    const sent = suitesToSend([newTestSuite('a1'), newTestSuite('a1')], [tests('a1')], stages)
    expect(sent).toHaveLength(1)
  })
})

describe('incompleteSuite', () => {
  it('没写运行命令、没有用例或根本没有那一套，都报出那个阶段', () => {
    expect(incompleteSuite([{ ...newTestSuite('a1'), runCmd: ' ' }], [tests('a1')], stages)).toBe('a1')
    expect(incompleteSuite([{ ...newTestSuite('a1'), cases: [] }], [tests('a1')], stages)).toBe('a1')
    expect(incompleteSuite([], [tests('b2')], stages)).toBe('b2')
    expect(incompleteSuite([newTestSuite('a1')], [tests('a1')], stages)).toBeUndefined()
  })
})

describe('suitesAfterStageChange', () => {
  it('删掉一个阶段，它的那一套跟着删；还在的阶段原样', () => {
    const next = suitesAfterStageChange([newTestSuite('a1'), newTestSuite('b2')], ['a1', 'b2'], ['a1'])
    expect(next.map((s) => s.stage)).toEqual(['a1'])
  })

  it('从不分阶段加第一个阶段，原来那一套归它', () => {
    const next = suitesAfterStageChange([newTestSuite(null)], [], ['a1'])
    expect(next.map((s) => s.stage)).toEqual(['a1'])
  })
})
