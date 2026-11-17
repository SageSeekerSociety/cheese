/**
 * 挂了测试点的题怎么批改、学生看到什么（方案 §批改窗口、§看结果）：
 *
 * - 测试点那一项按这一版的评测结果折算（通过分值 ÷ 总分值 × 行分值），老师只填手动几项，
 *   总分自动合计；不改测试分时不带调整。
 * - 老师改测试分要写原因，不写就拦住；写了，调整跟着评审一起交。
 * - 学生看到评测结果：隐藏用例不能点开；老师调整过的分连原因一起看到。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const api = vi.hoisted(() => ({
  listSubmissions: vi.fn(),
  postSubmissionReview: vi.fn(),
  patchSubmissionReview: vi.fn(),
  deleteSubmissionReview: vi.fn(),
}))
vi.mock('@/api/taskBoard', () => ({ TaskBoardApi: api }))

const judgeApi = vi.hoisted(() => ({ submissionJudge: vi.fn() }))
vi.mock('@/api/taskJudge', () => ({ TaskJudgeApi: judgeApi }))

import type { TaskRubricLine, TaskTestSuite } from '@/types'

import TaskSubmissionHistory from '../TaskSubmissionHistory.vue'

import i18n from '@/i18n'
import { newTestSuite } from '@/lib/taskTestSuite'

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})

const rubric: TaskRubricLine[] = [
  { item: '正确性', points: 60, criteria: '', stage: null, kind: 'tests' },
  { item: '代码质量', points: 20, criteria: '', stage: null },
  { item: '实验报告', points: 20, criteria: '', stage: null },
]
const suite: TaskTestSuite = { ...newTestSuite(null), revision: 1 }

function submission(review: unknown) {
  api.listSubmissions.mockResolvedValue({
    submissions: [
      { id: 77, version: 1, stage: null, createdAt: Date.now(), content: [], submitter: { nickname: 'x' }, review },
    ],
    page: { page_start: 0, page_size: 10, has_more: false, total: 1 },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  submission({ reviewed: false })
  api.postSubmissionReview.mockResolvedValue({})
  judgeApi.submissionJudge.mockResolvedValue({
    judge: {
      state: 'done',
      purpose: 'SUBMISSION',
      codeSha: 'abcdef1234567890',
      suiteRevision: 1,
      passedPoints: 70,
      totalPoints: 100,
      createdAt: 0,
      startedAt: 0,
      finishedAt: 0,
      machineMs: 12000,
      passedCases: 1,
      caseCount: 2,
      cases: [
        { name: '样例 1', visibility: 'SAMPLE', status: 'ok', points: 70, ms: 12, actual: '3\n' },
        { name: '隐藏用例 1', visibility: 'HIDDEN', status: 'wrong', points: 30, ms: 15 },
      ],
    },
  })
})

afterEach(cleanup)

function mount(reviewable: boolean) {
  render(TaskSubmissionHistory, {
    props: { taskId: 5, participantId: 9, reviewable, rubric, testSuite: [suite] },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const scoreInput = () => screen.getByLabelText(/tasks\.submissionHistory\.manualScore/) as HTMLInputElement
const submit = () => fireEvent.click(screen.getByRole('button', { name: 'tasks.submissionHistory.submit' }))

describe('批改挂了测试点的一版', () => {
  it('测试点那一项按评测结果折算，老师只填手动几项，总分自动合计', async () => {
    mount(true)
    await waitFor(() => expect(screen.getByTestId('task-review-tests-score').textContent).toContain('42 / 60'))

    await fireEvent.update(scoreInput(), '38')
    await waitFor(() => expect(screen.getByTestId('task-review-total').textContent).toContain('80 / 100'))

    await submit()
    await waitFor(() => expect(api.postSubmissionReview).toHaveBeenCalledTimes(1))
    const body = api.postSubmissionReview.mock.calls[0][3]
    expect(body).toMatchObject({ accepted: true, score: 38, testsOverride: null })
  })

  it('改测试分要写原因；写了，调整跟着评审一起交，总分跟着变', async () => {
    mount(true)
    await screen.findByTestId('task-review-tests-adjust')
    await fireEvent.update(scoreInput(), '38')
    await fireEvent.click(screen.getByTestId('task-review-tests-adjust'))

    const override = screen.getByTestId('task-review-tests-override').querySelector('input') as HTMLInputElement
    await fireEvent.update(override, '48')
    await waitFor(() => expect(screen.getByTestId('task-review-total').textContent).toContain('86 / 100'))

    await submit()
    await screen.findByText('tasks.judge.review.reasonRequired')
    expect(api.postSubmissionReview).not.toHaveBeenCalled()

    const reason = screen.getByTestId('task-review-tests-reason').querySelector('textarea') as HTMLTextAreaElement
    await fireEvent.update(reason, '用例 4 题面有歧义')
    await submit()
    await waitFor(() => expect(api.postSubmissionReview).toHaveBeenCalledTimes(1))
    expect(api.postSubmissionReview.mock.calls[0][3].testsOverride).toEqual({ score: 48, reason: '用例 4 题面有歧义' })
  })
})

describe('学生看这一版', () => {
  it('看到评测结果，隐藏用例点不开；老师调整过的分连原因一起看到', async () => {
    submission({
      reviewed: true,
      detail: {
        accepted: true,
        score: 86,
        comment: '',
        autoScores: {
          manual: 38,
          final: true,
          lines: [
            {
              index: 0,
              kind: 'tests',
              points: 60,
              score: 48,
              passedPoints: 70,
              totalPoints: 100,
              adjusted: true,
              reason: '用例 4 题面有歧义',
            },
          ],
        },
      },
    })
    mount(false)

    await screen.findByTestId('task-submission-judge')
    expect(screen.getByTestId('task-judge-passed').textContent).toBe('1/2')
    // 样例能点开，隐藏用例没有那颗展开按钮。
    expect(screen.getAllByRole('button', { name: /tasks\.judge\.result\.toggle/ })).toHaveLength(1)

    expect(screen.getByTestId('task-saved-tests-score').textContent).toContain('48 / 60')
    expect(screen.getByTestId('task-saved-tests-reason').textContent).toContain('用例 4 题面有歧义')
  })
})
