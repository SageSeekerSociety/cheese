/**
 * Reviewing a submission: what the reviewer must fill in, and what happens when
 * something is missing.
 *
 * - A score belongs to a passed submission (the result reads 「已通过 N 分」, a
 *   rejection reads 「已驳回」 with no number), so rejecting asks for no score.
 * - Passing asks for one, and says so before 提交 is pressed.
 * - Pressing 提交 with the score missing says what is missing instead of doing
 *   nothing.
 * - Changing a saved review to the other verdict does not carry the old
 *   verdict's comment or score over; changing back brings them back.
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const api = vi.hoisted(() => ({
  listSubmissions: vi.fn(),
  postSubmissionReview: vi.fn(),
  patchSubmissionReview: vi.fn(),
  deleteSubmissionReview: vi.fn(),
}))
vi.mock('@/api/taskBoard', () => ({ TaskBoardApi: api }))

import TaskSubmissionHistory from '../TaskSubmissionHistory.vue'

import i18n from '@/i18n'

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

beforeEach(() => {
  vi.clearAllMocks()
  api.listSubmissions.mockResolvedValue({
    submissions: [
      {
        id: 77,
        version: 1,
        createdAt: Date.now(),
        content: [],
        submitter: { nickname: 'someone' },
        review: { reviewed: false },
      },
    ],
    page: { page_start: 0, page_size: 10, has_more: false, total: 1 },
  })
  api.postSubmissionReview.mockResolvedValue({})
  api.patchSubmissionReview.mockResolvedValue({})
})

afterEach(cleanup)

/** Pick 通过 or 驳回 the way a click does: the radio becomes checked and says so. */
async function choose(key: 'accept' | 'reject') {
  const radio = screen.getByLabelText(`tasks.submissionHistory.${key}`) as HTMLInputElement
  radio.checked = true
  await fireEvent.input(radio)
  await fireEvent.change(radio)
}

/** The latest version already carries a saved review. */
function savedReview(detail: { accepted: boolean; score: number; comment: string }) {
  api.listSubmissions.mockResolvedValue({
    submissions: [
      {
        id: 77,
        version: 1,
        createdAt: Date.now(),
        content: [],
        submitter: { nickname: 'someone' },
        review: { reviewed: true, detail },
      },
    ],
    page: { page_start: 0, page_size: 10, has_more: false, total: 1 },
  })
}

const commentInput = () => screen.getByLabelText('tasks.submissionHistory.comment') as HTMLTextAreaElement

async function mountReview() {
  render(TaskSubmissionHistory, {
    props: { taskId: 5, participantId: 9, reviewable: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await screen.findByText(/^tasks\.submissionHistory\.(review|editReview)$/)
}

const scoreInput = () =>
  screen.queryByLabelText(/tasks\.submissionHistory\.\d*(score|manualScore)/) as HTMLInputElement | null

describe('reviewing a submission', () => {
  it('rejects without asking for a score', async () => {
    await mountReview()
    await choose('reject')
    await waitFor(() => expect(scoreInput()).toBeNull())

    await fireEvent.click(screen.getByRole('button', { name: 'tasks.submissionHistory.submit' }))

    await waitFor(() => expect(api.postSubmissionReview).toHaveBeenCalledTimes(1))
    const [, , submissionId, body] = api.postSubmissionReview.mock.calls[0]
    expect(submissionId).toBe(77)
    expect(body.accepted).toBe(false)
  })

  it('marks the score required before anything is pressed when passing', async () => {
    await mountReview()
    await choose('accept')
    const input = scoreInput()
    expect(input).not.toBeNull()
    expect(input!.required || input!.getAttribute('aria-required') === 'true').toBe(true)
  })

  it('says the score is missing when passing without one', async () => {
    await mountReview()
    await choose('accept')

    await fireEvent.click(screen.getByRole('button', { name: 'tasks.submissionHistory.submit' }))

    expect(await screen.findByText('tasks.submissionHistory.scoreRequired')).toBeTruthy()
    expect(api.postSubmissionReview).not.toHaveBeenCalled()
  })

  it('sends the score given when passing', async () => {
    await mountReview()
    await choose('accept')
    await fireEvent.update(scoreInput()!, '88')

    await fireEvent.click(screen.getByRole('button', { name: 'tasks.submissionHistory.submit' }))

    await waitFor(() => expect(api.postSubmissionReview).toHaveBeenCalledTimes(1))
    const body = api.postSubmissionReview.mock.calls[0][3]
    expect(body).toMatchObject({ accepted: true, score: 88 })
  })

  it('changing a rejection to a pass does not send the rejection comment with it', async () => {
    savedReview({ accepted: false, score: 0, comment: '没有处理空输入' })
    await mountReview()
    await waitFor(() => expect(commentInput().value).toBe('没有处理空输入'))

    await choose('accept')
    await waitFor(() => expect(commentInput().value).toBe(''))
    expect(scoreInput()!.value).toBe('')
    await fireEvent.update(scoreInput()!, '90')
    await fireEvent.click(screen.getByRole('button', { name: 'tasks.submissionHistory.submit' }))

    await waitFor(() => expect(api.patchSubmissionReview).toHaveBeenCalledTimes(1))
    expect(api.patchSubmissionReview.mock.calls[0][3]).toEqual({ accepted: true, score: 90, comment: '' })
  })

  it('changing back to the saved verdict brings its comment back', async () => {
    savedReview({ accepted: false, score: 0, comment: '没有处理空输入' })
    await mountReview()
    await waitFor(() => expect(commentInput().value).toBe('没有处理空输入'))

    await choose('accept')
    await waitFor(() => expect(commentInput().value).toBe(''))
    await choose('reject')

    await waitFor(() => expect(commentInput().value).toBe('没有处理空输入'))
  })

  it('a comment written for the new verdict stays when the verdict is changed again', async () => {
    savedReview({ accepted: true, score: 80, comment: '思路清楚' })
    await mountReview()
    await waitFor(() => expect(commentInput().value).toBe('思路清楚'))

    await choose('reject')
    await waitFor(() => expect(commentInput().value).toBe(''))
    await fireEvent.update(commentInput(), '第二题算错了')
    await choose('accept')

    await waitFor(() => expect(scoreInput()!.value).toBe('80'))
    expect(commentInput().value).toBe('第二题算错了')
  })
})

// ── 按额度计分的题：评审里填的是手动项那一截 ────────────────────────────────────
//
// 题目在那一版的阶段上有额度那一行时，后端 `usage_score.compose_review` 把老师填的分
// （手动几项，0 到这一阶段手动项之和）加上额度那一行自动算出来的分。所以框上的标签写着
// 手动项、上限是手动项之和，而不是 0–100；填超了当场报错，不给后端 400 的机会。

/** A task whose rubric gives the stage a manual item worth 70 and a usage line worth 30. */
const RUBRIC = [
  { item: '正确性', points: 70, criteria: '照做', stage: null },
  {
    item: '额度用量',
    points: 30,
    criteria: '',
    stage: null,
    kind: 'usage' as const,
    config: { mode: 'threshold' as const, full: 0, zero: 100 },
  },
]

/** 列表接口把这一份的额度放在顶层（`usage_score.usage_of_claims` 的样子）。 */
function withUsage(usage: unknown) {
  api.listSubmissions.mockResolvedValue({
    submissions: [
      {
        id: 77,
        version: 1,
        createdAt: Date.now(),
        content: [],
        submitter: { nickname: 'someone' },
        review: { reviewed: false },
      },
    ],
    page: { page_start: 0, page_size: 10, has_more: false, total: 1 },
    usage,
  })
}

async function mountUsageReview() {
  render(TaskSubmissionHistory, {
    props: { taskId: 5, participantId: 9, reviewable: true, rubric: RUBRIC },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await screen.findByText(/^tasks\.submissionHistory\.(review|editReview)$/)
}

describe('a challenge scored by credits used', () => {
  beforeEach(() => {
    withUsage({
      credits: 40,
      line: { index: 1, points: 30, mode: 'threshold', full: 0, zero: 100 },
      score: 12,
      final: true,
    })
  })

  it('says the score is the manual items and caps it at their total', async () => {
    await mountUsageReview()
    await choose('accept')

    // 标签换成「手动项评分（0–70）」那一句，框的上限也是 70（不是 100）。
    expect(screen.getByLabelText(/tasks\.submissionHistory\.manualScore/, { exact: false })).toBeTruthy()
    expect(scoreInput()!.getAttribute('max')).toBe('70')
  })

  it('refuses a score above the manual total before it reaches the API', async () => {
    await mountUsageReview()
    await choose('accept')
    await fireEvent.update(scoreInput()!, '80')

    await fireEvent.click(screen.getByRole('button', { name: 'tasks.submissionHistory.submit' }))

    expect(await screen.findByText('tasks.submissionHistory.scoreRequiredFor')).toBeTruthy()
    expect(api.postSubmissionReview).not.toHaveBeenCalled()
  })

  it('sends the manual subtotal when it is within the cap', async () => {
    await mountUsageReview()
    await choose('accept')
    await fireEvent.update(scoreInput()!, '66')

    await fireEvent.click(screen.getByRole('button', { name: 'tasks.submissionHistory.submit' }))

    await waitFor(() => expect(api.postSubmissionReview).toHaveBeenCalledTimes(1))
    expect(api.postSubmissionReview.mock.calls[0][3]).toMatchObject({ accepted: true, score: 66 })
  })

  it('offers the estimate, the rule and the per-model split', async () => {
    withUsage({
      credits: 40,
      line: { index: 1, points: 30, mode: 'threshold', full: 0, zero: 100 },
      score: 12,
      final: true,
      models: [{ model: 'gpt-x', credits: 40 }],
    })
    await mountUsageReview()

    expect(screen.getByText('tasks.submissionHistory.usageEyebrowTeacher')).toBeTruthy()
    expect(screen.getByText('tasks.submissionHistory.usageByModel')).toBeTruthy()
    expect(screen.getAllByText('40').length).toBeGreaterThan(0)
  })

  it('editing a saved review fills back the manual part, not the total', async () => {
    // 库里那条评审的 `score` 是总分 78（手动 66 + 自动 12）。
    api.listSubmissions.mockResolvedValue({
      submissions: [
        {
          id: 77,
          version: 1,
          createdAt: Date.now(),
          content: [],
          submitter: { nickname: 'someone' },
          review: {
            reviewed: true,
            detail: {
              accepted: true,
              score: 78,
              comment: '还行',
              autoScores: {
                manual: 66,
                final: true,
                lines: [{ index: 1, kind: 'usage', points: 30, score: 12, credits: 40, mode: 'threshold' }],
              },
            },
          },
        },
      ],
      page: { page_start: 0, page_size: 10, has_more: false, total: 1 },
      usage: {
        credits: 40,
        line: { index: 1, points: 30, mode: 'threshold', full: 0, zero: 100 },
        score: 12,
        final: true,
      },
    })
    await mountUsageReview()

    await waitFor(() => expect(scoreInput()!.value).toBe('66'))
  })

  it('a challenge without a usage line still asks for 0–100', async () => {
    await mountReview()
    await choose('accept')
    expect(screen.getByLabelText(/tasks\.submissionHistory\.score/, { exact: false })).toBeTruthy()
    expect(scoreInput()!.getAttribute('max')).toBe('100')
  })
})
