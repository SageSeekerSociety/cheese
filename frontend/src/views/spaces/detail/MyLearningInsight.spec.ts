// 「我的学习洞察」那一页。守的是：
//   1. 有结果：最该补的那个知识点带着归纳和依据摆在最上面，全部知识点按清单顺序列出，
//      没涉及的说一句「还没涉及」；
//   2. 标记不准：点了把那条判断的编号交给 `POST .../dispute`，然后重读一遍；
//   3. 四种状态各是各的：读不出来（重试）、没开学情分析、开着但还没有结果、有结果。
//   4. 第 4 步的「生成巩固题」「复习提醒」不出现。
import type { MyLearningInsight } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const readMyLearningInsight = vi.fn()
const disputeLearningJudgment = vi.fn()

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    readMyLearningInsight: (...a: unknown[]) => readMyLearningInsight(...a),
    disputeLearningJudgment: (...a: unknown[]) => disputeLearningJudgment(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { spaceId: '11' } }) }))

import MyLearningInsightPage from './MyLearningInsight.vue'

import i18n, { setLocale } from '@/i18n'

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
    sources: { messagesRead: 12, tasks: [{ taskId: 3, name: '第 3 题', messagesRead: 12 }] },
    ...overrides,
  }
}

function mount() {
  render(MyLearningInsightPage, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
}

describe('我的学习洞察', () => {
  beforeEach(() => {
    setLocale('zh-CN')
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
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
    expect(screen.queryByText(/巩固题|复习提醒/)).toBeNull()
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
})
