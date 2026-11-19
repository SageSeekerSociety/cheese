import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import LearningClassTasks from './LearningClassTasks.vue'

import i18n, { setLocale } from '@/i18n'

function row(over: Record<string, unknown>) {
  return {
    taskId: 1,
    name: '两数之和',
    analysed: true,
    claims: 10,
    completionRate: 0.8,
    avgTurns: 3,
    avgHours: 2,
    stuckRate: 0.1,
    answerSeekers: 0,
    ...over,
  }
}

function mount(tasks: ReturnType<typeof row>[]) {
  return render(LearningClassTasks, {
    props: { data: { enabled: true, minPeople: 3, tasks } as never },
    global: { plugins: [i18n] },
  })
}

/** 表头的每一格，和第 n 行的每一格。 */
const heads = () => Array.from(document.querySelectorAll('th')).map((th) => th.textContent?.trim())
const cells = (n: number) =>
  Array.from(document.querySelectorAll('tbody tr')[n]?.querySelectorAll('td') ?? []).map((td) => td.textContent?.trim())

describe('学情分析「题目难度」的测试通过率', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => cleanup())

  it('有题评过测试时多一列全班平均，不足 3 份的显示「—」', () => {
    mount([
      row({ taskId: 1, testPassRate: 0.5333, testJudged: 12 }),
      row({ taskId: 2, name: '人少的题', testPassRate: null, testJudged: 2 }),
      row({ taskId: 3, name: '没有测试点' }),
    ])

    expect(heads()).toContain('测试通过率')
    expect(cells(0).at(-1)).toBe('53%')
    expect(cells(1).at(-1)).toBe('—')
    expect(cells(2).at(-1)).toBe('—')
    expect(document.body.textContent).toContain('不足 3 份不显示')
  })

  it('没有一道题评过测试：表和原来一样，没有这一列', () => {
    mount([row({})])

    expect(heads()).not.toContain('测试通过率')
    expect(cells(0)).toHaveLength(7)
  })
})
