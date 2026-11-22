// 测试脚本和命令判分的评测结果：逐个测试的状态和失败信息、命令判分的分和各项、判分失败时
// 老师看程序输出、学生只看出题人允许的那几行；输入/输出用例照旧。
import type { TaskJudgeRun } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import TaskJudgeResult from '../TaskJudgeResult.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const base: TaskJudgeRun = {
  state: 'done',
  purpose: 'submission',
  codeSha: 'abcdef1234567890',
  suiteRevision: 1,
  passedPoints: 0,
  totalPoints: 0,
  createdAt: 0,
  startedAt: 0,
  finishedAt: 0,
  machineMs: 1000,
}

function mount(run: TaskJudgeRun, props: Record<string, unknown> = {}) {
  return render(TaskJudgeResult, {
    props: { run, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('测试脚本的结果', () => {
  const run: TaskJudgeRun = {
    ...base,
    passedPoints: 30,
    totalPoints: 70,
    passedCases: 1,
    caseCount: 3,
    cases: [
      { name: '加法', testId: 'test_calc::test_add', visibility: 'SAMPLE', status: 'ok', points: 30, ms: 12 },
      {
        name: '除零',
        testId: 'test_calc::test_div',
        visibility: 'SAMPLE',
        status: 'wrong',
        points: 40,
        message: 'ZeroDivisionError: division by zero',
      },
      {
        name: 'test_slow',
        testId: 'test_slow',
        visibility: 'HIDDEN',
        status: 'wrong',
        points: 0,
        skipped: true,
        unscored: true,
      },
    ],
  }

  it('一行一个测试：通过几个、跳过的说「跳过」、没配分的标出来；有失败信息的能点开', async () => {
    mount(run)
    expect(screen.getByTestId('task-judge-passed').textContent).toBe('1/3')
    expect(screen.getByText('测试通过 · 分值 30/70')).toBeTruthy()
    const statuses = screen.getAllByTestId('task-judge-case-status').map((el) => el.textContent?.trim())
    expect(statuses).toEqual(['通过', '答案错误', '跳过'])
    expect(screen.getByText('未计分')).toBeTruthy()
    expect(screen.getByText('test_calc::test_add')).toBeTruthy()

    // 只有带失败信息的那一行有展开按钮。
    const toggles = screen.getAllByRole('button', { name: /展开或收起/ })
    expect(toggles).toHaveLength(1)
    await fireEvent.click(toggles[0]!)
    expect(screen.getByTestId('task-judge-message').textContent).toBe('ZeroDivisionError: division by zero')
    // 没有输入、期望输出那几格。
    expect(screen.queryByText('期望输出')).toBeNull()
  })
})

describe('命令判分的结果', () => {
  it('顶上是判分命令给的分，下面是各项；老师看得到不公开的那一项', () => {
    mount(
      {
        ...base,
        passedPoints: 7,
        totalPoints: 10,
        cases: [
          { name: '编译', visibility: 'SAMPLE', status: 'ok', points: 2, score: 2, maxScore: 2 },
          { name: '功能', visibility: 'SAMPLE', status: 'wrong', points: 5, score: 3, maxScore: 5 },
          { name: '压力', visibility: 'SAMPLE', status: 'ok', points: 3, score: 2, maxScore: 3, private: true },
        ],
      },
      { teacher: true }
    )
    expect(screen.getByTestId('task-judge-score').textContent).toBe('7/10')
    expect(screen.getAllByTestId('task-judge-item-score').map((el) => el.textContent)).toEqual(['2/2', '3/5', '2/3'])
    expect(screen.getByText('不对学生公开')).toBeTruthy()
    expect(screen.queryByTestId('task-judge-hidden-items')).toBeNull()
    expect(screen.queryAllByTestId('task-judge-case-status')).toHaveLength(0)
  })

  it('学生那边：不公开的那一项不在，只说还有几分来自不公开的检查项；带小数的分除回来', () => {
    mount({
      ...base,
      passedPoints: 750,
      totalPoints: 1000,
      cases: [{ name: '功能', visibility: 'SAMPLE', status: 'wrong', points: 6, score: 5.5, maxScore: 6 }],
    })
    expect(screen.getByTestId('task-judge-score').textContent).toBe('7.5/10')
    expect(screen.getByTestId('task-judge-hidden-items').textContent).toContain('另有 4 分来自不公开的检查项')
  })
})

describe('判分失败', () => {
  const failed: TaskJudgeRun = {
    ...base,
    scoreFailed: true,
    error: { key: 'judgeScoreMissing' },
    compileOutput: 'line 1\nline 2\nTraceback ...',
    cases: [],
  }

  it('老师：说判分失败、不按 0 分算，下面是程序输出', () => {
    mount(failed, { teacher: true })
    const banner = screen.getByTestId('task-judge-score-failed')
    expect(banner.textContent).toContain('判分失败')
    expect(banner.textContent).toContain('手动给测试分')
    expect(screen.getByTestId('task-judge-output').textContent).toContain('Traceback')
    expect(screen.queryByTestId('task-judge-passed')).toBeNull()
  })

  it('学生：只看出题人允许的前几行；没给就不显示输出', () => {
    mount({ ...failed, compileOutput: null, studentOutput: 'line 1' })
    expect(screen.getByTestId('task-judge-score-failed').textContent).toContain('老师会手动给分')
    expect(screen.getByTestId('task-judge-output').textContent).toBe('line 1')
    cleanup()
    mount({ ...failed, compileOutput: null })
    expect(screen.queryByTestId('task-judge-output')).toBeNull()
  })
})

describe('输入/输出用例照旧', () => {
  it('通过几个用例、用例分；能点开看输入输出', async () => {
    mount(
      {
        ...base,
        passedPoints: 10,
        totalPoints: 10,
        cases: [
          { name: '样例 1', visibility: 'SAMPLE', status: 'ok', points: 10, input: '1 2', expected: '3', actual: '3' },
        ],
      },
      { teacher: true }
    )
    expect(screen.getByTestId('task-judge-passed').textContent).toBe('1/1')
    await fireEvent.click(screen.getByRole('button', { name: /展开或收起/ }))
    expect(screen.getByText('期望输出')).toBeTruthy()
  })
})
