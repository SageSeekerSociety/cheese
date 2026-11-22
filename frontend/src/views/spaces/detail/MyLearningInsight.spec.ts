// 「我的学习洞察」那一页。守的是：
//   1. 有结果：最该补的那个知识点带着归纳和依据摆在最上面，全部知识点按清单顺序列出，
//      没涉及的说一句「还没涉及」；
//   2. 标记不准：点了把那条判断的编号交给 `POST .../dispute`，然后重读一遍；
//   3. 四种状态各是各的：读不出来（重试）、没开学情分析、开着但还没有结果、有结果。
//   4. 巩固练习：出一组 → 答一道当场看到对错和讲解 → 都答完「做完了」，结果并进判断（页面重读）；
//   5. 复习提醒：到期的那条「再做一组」出的是复习那一组；没到期时说下一次是哪天；
//   6. 「先看一段讲解」读回来的那一段摆在最该补的下面；今天的次数用完时说服务端那句话。
import type { LearningPracticeSet, MyLearningInsight, MyLearningPractice } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const readMyLearningInsight = vi.fn()
const disputeLearningJudgment = vi.fn()
const readMyLearningPractice = vi.fn()
const startLearningPractice = vi.fn()
const readLearningPractice = vi.fn()
const answerLearningPractice = vi.fn()
const completeLearningPractice = vi.fn()
const explainLearningPoint = vi.fn()
const toastError = vi.fn()

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    readMyLearningInsight: (...a: unknown[]) => readMyLearningInsight(...a),
    disputeLearningJudgment: (...a: unknown[]) => disputeLearningJudgment(...a),
    readMyLearningPractice: (...a: unknown[]) => readMyLearningPractice(...a),
    startLearningPractice: (...a: unknown[]) => startLearningPractice(...a),
    readLearningPractice: (...a: unknown[]) => readLearningPractice(...a),
    answerLearningPractice: (...a: unknown[]) => answerLearningPractice(...a),
    completeLearningPractice: (...a: unknown[]) => completeLearningPractice(...a),
    explainLearningPoint: (...a: unknown[]) => explainLearningPoint(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({
  toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a), warning: vi.fn() },
}))

const joinedTasks = vi.fn()
const taskDetail = vi.fn()
const push = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { spaceId: '11' } }),
  useRouter: () => ({ push }),
}))

vi.mock('@/api/taskBoard', () => ({
  TaskBoardApi: {
    joined: (...a: unknown[]) => joinedTasks(...a),
    detail: (...a: unknown[]) => taskDetail(...a),
  },
}))

// 这一页的推送（实时分析）在用例里由 `pushLearningLive` 手动叫：不开真连接。
vi.mock('@/lib/learningLive', () => import('@/test/fakeLearningLive'))

import MyLearningInsightPage from './MyLearningInsight.vue'

import i18n, { setLocale, t } from '@/i18n'

function insight(overrides: Partial<MyLearningInsight> = {}): MyLearningInsight {
  return {
    enabled: true,
    updatedAt: Date.now() - 60_000,
    focusKnowledgePointId: 1,
    knowledgePoints: [
      {
        id: 1,
        name: '相关子查询',
        judgment: {
          id: 501,
          level: 'weak',
          summary: '你还不清楚内层怎样引用外层那一行。',
          disputed: false,
          analyzedAt: Date.now(),
          evidence: [
            {
              blockId: 'b1',
              taskId: 3,
              taskName: '第 3 题',
              quote: '子查询里怎么引用外层那一行我一直没搞懂',
              createdAt: Date.now(),
            },
          ],
        },
      },
      { id: 2, name: '索引', judgment: null },
    ],
    frequentQuestions: [
      { knowledgePointId: 1, knowledgePointName: '相关子查询', kind: 'concept', count: 4, example: '怎么引用外层的列' },
    ],
    realtime: { state: 'live', callsToday: 5, dailyCalls: 2000 },
    sources: { messagesRead: 12, tasks: [{ taskId: 3, name: '第 3 题', messagesRead: 12 }] },
    ...overrides,
  }
}

function overview(overrides: Partial<MyLearningPractice> = {}): MyLearningPractice {
  return { enabled: true, dailyLimit: 8, usedToday: 0, open: [], due: [], upcoming: null, ...overrides }
}

function practiceSet(overrides: Partial<LearningPracticeSet> = {}): LearningPracticeSet {
  return {
    id: 70,
    knowledgePointId: 1,
    knowledgePointName: '相关子查询',
    source: 'self',
    ready: true,
    done: false,
    questions: [1, 2, 3].map((n) => ({
      stem: `第 ${n} 题题干`,
      options: ['用外层的别名', '用 GROUP BY'],
      chosen: null,
      correct: null,
      answer: null,
      explanation: null,
    })),
    correct: null,
    levelBefore: null,
    levelAfter: null,
    createdAt: Date.now(),
    completedAt: null,
    ...overrides,
  }
}

function mount() {
  render(MyLearningInsightPage, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
}

describe('我的学习洞察', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    vi.stubGlobal('visualViewport', new EventTarget())
    readMyLearningPractice.mockResolvedValue(overview())
    joinedTasks.mockResolvedValue([])
    taskDetail.mockResolvedValue({ task: { space: { id: 11 } } })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it('最该补的那条带着归纳和依据，没涉及的知识点说一句', async () => {
    readMyLearningInsight.mockResolvedValue(insight())
    mount()

    const focus = await screen.findByTestId('insight-focus')
    expect(focus.textContent).toContain('相关子查询')
    expect(focus.textContent).toContain('薄弱')
    expect(focus.textContent).toContain('你还不清楚内层怎样引用外层那一行。')
    expect(focus.textContent).toContain('子查询里怎么引用外层那一行我一直没搞懂')
    expect(screen.getByText('你的对话里还没涉及这个知识点')).toBeTruthy()
    expect(screen.getByTestId('insight-frequent').textContent).toContain('4 次')
  })

  it('标记不准：交的是这条判断，标完重读一遍', async () => {
    readMyLearningInsight.mockResolvedValueOnce(insight())
    const after = insight({ focusKnowledgePointId: null })
    after.knowledgePoints[0].judgment!.disputed = true
    readMyLearningInsight.mockResolvedValueOnce(after)
    disputeLearningJudgment.mockResolvedValue({ judgmentId: 501, disputed: true })
    mount()

    await fireEvent.click(await screen.findByTestId('insight-dispute-focus'))

    await waitFor(() => expect(disputeLearningJudgment).toHaveBeenCalledWith(11, 501))
    await waitFor(() => expect(screen.getByText('你标记过这条不准，下次分析时会复核')).toBeTruthy())
    expect(readMyLearningInsight).toHaveBeenCalledTimes(2)
    expect(screen.getByText('目前没有要补的知识点。')).toBeTruthy()
  })

  it('开着但还没有结果：空状态，右栏照样说清读什么', async () => {
    readMyLearningInsight.mockResolvedValue(
      insight({
        updatedAt: null,
        focusKnowledgePointId: null,
        knowledgePoints: [{ id: 1, name: '相关子查询', judgment: null }],
        frequentQuestions: [],
        sources: { messagesRead: 0, tasks: [] },
      })
    )
    mount()

    expect(await screen.findByTestId('insight-empty')).toBeTruthy()
    expect(screen.getByTestId('insight-sources').textContent).toContain('仅你领题时同意的导师')
    expect(screen.queryByTestId('insight-focus')).toBeNull()
  })

  it('没开学情分析：明说没开', async () => {
    readMyLearningInsight.mockResolvedValue(
      insight({ enabled: false, updatedAt: null, knowledgePoints: [], focusKnowledgePointId: null })
    )
    mount()

    expect(await screen.findByText('这个空间没有开启学情分析')).toBeTruthy()
    expect(screen.queryByTestId('insight-sources')).toBeNull()
  })

  it('读不出来：给重试，不画成「还没有结果」', async () => {
    readMyLearningInsight.mockRejectedValueOnce(new Error('offline'))
    readMyLearningInsight.mockResolvedValueOnce(insight())
    mount()

    const retry = await screen.findByRole('button', { name: /重试/ })
    expect(screen.queryByTestId('insight-empty')).toBeNull()
    await fireEvent.click(retry)
    expect(await screen.findByTestId('insight-focus')).toBeTruthy()
  })

  it('巩固练习：答一道当场看到讲解，做完并进判断，页面重读', async () => {
    readMyLearningInsight.mockResolvedValue(insight())
    startLearningPractice.mockResolvedValue(practiceSet())
    answerLearningPractice.mockImplementation((_s: number, _p: number, data: { question: number; choice: number }) =>
      Promise.resolve({
        question: data.question,
        chosen: data.choice,
        correct: data.choice === 0,
        answer: 0,
        explanation: `讲解 ${data.question + 1}`,
      })
    )
    completeLearningPractice.mockResolvedValue({
      practiceId: 70,
      correct: 3,
      total: 3,
      levelBefore: 'weak',
      levelAfter: 'shaky',
      nextReviewOn: '2026-11-20',
    })
    mount()

    await fireEvent.click(await screen.findByTestId('insight-practice'))
    await waitFor(() => expect(startLearningPractice).toHaveBeenCalledWith(11, { knowledgePointId: 1, source: 'self' }))
    const dialog = await screen.findByTestId('learning-practice')
    expect(within(dialog).getAllByTestId('practice-question')).toHaveLength(3)
    // 没答之前，讲解不在页面上；「做完了」点不了。
    expect(within(dialog).queryByTestId('practice-explanation')).toBeNull()
    expect((within(dialog).getByTestId('practice-done') as HTMLButtonElement).disabled).toBe(true)

    for (const question of [0, 1, 2]) {
      const options = within(within(dialog).getAllByTestId('practice-question')[question]).getAllByTestId(
        'practice-option'
      )
      await fireEvent.click(options[0])
      await waitFor(() => expect(within(dialog).getAllByTestId('practice-explanation')).toHaveLength(question + 1))
    }
    expect(within(dialog).getAllByTestId('practice-explanation')[0].textContent).toContain('讲解 1')

    await fireEvent.click(within(dialog).getByTestId('practice-done'))
    await waitFor(() => expect(completeLearningPractice).toHaveBeenCalledWith(11, 70))
    expect((await within(dialog).findByTestId('practice-result')).textContent).toContain('「薄弱」变成了「不稳」')
    await waitFor(() => expect(readMyLearningInsight).toHaveBeenCalledTimes(2))
  })

  it('今天实时分析到上限：这一页写明「今天实时分析到上限，明早统一更新」', async () => {
    readMyLearningInsight.mockResolvedValue(
      insight({ realtime: { state: 'capped', callsToday: 1800, dailyCalls: 2000 } })
    )
    mount()
    await screen.findByText(t('spaces.learningAnalysis.insight.realtime.capped'))
  })

  it('复习提醒：到期的再做一组出的是复习那一组；没到期时说下一次是哪天', async () => {
    readMyLearningInsight.mockResolvedValue(insight())
    readMyLearningPractice.mockResolvedValue(
      overview({ due: [{ knowledgePointId: 1, knowledgePointName: '相关子查询', dueOn: '2026-11-16', streak: 1 }] })
    )
    startLearningPractice.mockResolvedValue(practiceSet({ source: 'review' }))
    mount()

    const review = await screen.findByTestId('insight-review')
    await fireEvent.click(await within(review).findByRole('button', { name: '再做一组' }))
    await waitFor(() =>
      expect(startLearningPractice).toHaveBeenCalledWith(11, { knowledgePointId: 1, source: 'review' })
    )

    cleanup()
    readMyLearningPractice.mockResolvedValue(
      overview({ upcoming: { knowledgePointId: 1, knowledgePointName: '相关子查询', dueOn: '2026-11-20', streak: 1 } })
    )
    mount()
    await waitFor(() => expect(screen.getByTestId('insight-review').textContent).toContain('「相关子查询」'))
    expect(screen.queryByRole('button', { name: '再做一组' })).toBeNull()
  })

  it('先看一段讲解：读回来的那一段摆在最该补的下面', async () => {
    readMyLearningInsight.mockResolvedValue(insight())
    explainLearningPoint.mockResolvedValue({ knowledgePointId: 1, text: '你可能以为内层看不见外层。', generatedAt: 1 })
    mount()

    await fireEvent.click(await screen.findByTestId('insight-explain'))
    expect((await screen.findByTestId('insight-explanation')).textContent).toContain('你可能以为内层看不见外层。')
    expect(explainLearningPoint).toHaveBeenCalledWith(11, { knowledgePointId: 1 })
  })

  it('今天的次数用完：说服务端那句话，不弹出空的练习', async () => {
    readMyLearningInsight.mockResolvedValue(insight())
    startLearningPractice.mockRejectedValue({
      isAxiosError: true,
      response: { status: 429, data: { code: 429, message: '你今天已经用了 8 次，明天再来' } },
    })
    mount()

    await fireEvent.click(await screen.findByTestId('insight-practice'))
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(screen.queryByTestId('learning-practice')).toBeNull()
  })

  it('领了这块板上的题时，顶上给一条回作业的路，点了去那道题', async () => {
    readMyLearningInsight.mockResolvedValue(insight())
    joinedTasks.mockResolvedValue([
      {
        id: 2465,
        title: 'SQL 聚合函数与分组',
        intro: null,
        deadline: null,
        myDeadline: null,
        approved: true,
        ended: false,
      },
      { id: 9, title: '别的板的题', intro: null, deadline: null, myDeadline: null, approved: true, ended: false },
    ])
    taskDetail.mockImplementation((id: number) =>
      Promise.resolve({ task: { space: id === 2465 ? { id: 11 } : { id: 12 } } })
    )
    mount()

    const back = await screen.findByTestId('insight-back-to-work')
    expect(back.textContent).toContain('SQL 聚合函数与分组')
    expect(back.textContent).not.toContain('别的板的题')
    await fireEvent.click(screen.getByTestId('insight-back-task'))
    expect(push).toHaveBeenCalledWith({
      name: 'TasksDetail',
      params: { spaceId: '11', taskId: '2465' },
    })
  })
})
