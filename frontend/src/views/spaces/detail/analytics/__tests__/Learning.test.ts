/** 学习那一格: 缺的如实报缺、勾中的带进提纲、每条都点得回原文。
 *
 * 这一份钉的是后端已经明确交代过的三件事 —— 队列两条来源各是什么、提纲里的讲次
 * 由勾选顺序决定、页面不拿别的数据顶替没有落库的那一维。
 */
import type {
  SpaceLearningFilters,
  SpaceLearningQuestion,
  SpaceLearningQueues,
  SpaceLearningStuckPoint,
} from '@/network/api/spaces/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getLearningFilters = vi.fn()
const getLearningQueues = vi.fn()
const getLearningQuestions = vi.fn()
const buildLearningOutline = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getLearningFilters: (...args: unknown[]) => getLearningFilters(...args),
    getLearningQueues: (...args: unknown[]) => getLearningQueues(...args),
    getLearningQuestions: (...args: unknown[]) => getLearningQuestions(...args),
    buildLearningOutline: (...args: unknown[]) => buildLearningOutline(...args),
  },
}))

const push = vi.fn()
const replace = vi.fn()
const route: { query: Record<string, string>; params: { spaceId: string } } = {
  query: {},
  params: { spaceId: '42' },
}

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push, replace }),
}))

import Learning from '../Learning.vue'

const question = (over: Partial<SpaceLearningQuestion> = {}): SpaceLearningQuestion => ({
  blockId: 'block-1',
  topicId: 'topic-1',
  projectId: 'project-1',
  projectName: '项目一',
  student: 'zhangsan',
  studentName: '张三',
  topicTitle: '第一次作业',
  knowledgePoint: '循环',
  createdAt: new Date('2026-03-01T10:00:00.000Z').getTime(),
  quote: '这个循环为什么停不下来',
  ...over,
})

const stuckPoint = (over: Partial<SpaceLearningStuckPoint> = {}): SpaceLearningStuckPoint => ({
  knowledgePoint: '循环',
  studentCount: 4,
  projectCount: 3,
  questionCount: 6,
  latestAt: new Date('2026-03-02T09:00:00.000Z').getTime(),
  example: question({ blockId: 'block-loop', quote: '循环条件写反了会怎么样' }),
  ...over,
})

const REVIEW_REASON =
  '平台没有记录这个信号：芝士判断「这道题我答不了」时不会写下任何东西。这一格等那个信号落库之后才会有内容。'

const filtersPayload: SpaceLearningFilters = {
  students: [{ handle: 'zhangsan', name: '张三' }],
  knowledgePoints: [{ categoryId: 7, name: '循环' }],
  projectCount: 3,
}

const queuesPayload = (over: Partial<SpaceLearningQueues> = {}): SpaceLearningQueues => ({
  reviewFlag: { available: false, reason: REVIEW_REASON, items: [] },
  stuckPoints: [
    stuckPoint(),
    stuckPoint({
      knowledgePoint: '数组',
      studentCount: 2,
      projectCount: 2,
      questionCount: 2,
      example: question({ blockId: 'block-array', quote: '数组越界为什么不报错' }),
    }),
  ],
  ...over,
})

const outlinePayload = {
  title: '程序设计基础 · 共性问题讲解提纲',
  sections: [
    {
      knowledgePoint: '循环',
      session: 1,
      excerpts: [question({ blockId: 'block-loop', quote: '循环条件写反了会怎么样' })],
    },
  ],
  missing: [],
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  push.mockReset()
  replace.mockReset()
  route.query = {}
  getLearningFilters.mockReset().mockResolvedValue({ data: filtersPayload })
  getLearningQueues.mockReset().mockResolvedValue({ data: queuesPayload() })
  getLearningQuestions.mockReset().mockResolvedValue({ data: { questions: [question()], total: 1 } })
  buildLearningOutline.mockReset().mockResolvedValue({ data: outlinePayload })
})

function mount() {
  return render(Learning, { global: { plugins: [vuetify] } })
}

describe('没有数据源的那一格如实报缺', () => {
  it('把后端写明的缺席理由原样显示出来', async () => {
    const { findByText, queryByText } = mount()

    await findByText(REVIEW_REASON)
    // 没有内容就是没有内容 —— 不许拿别的队列顶上。
    expect(queryByText('暂无学生标记的问题')).toBeNull()
  })
})

describe('待处理队列', () => {
  it('卡点按后端给的顺序排，示例发言点得回原文', async () => {
    const { container, findAllByText } = mount()

    await findAllByText('循环')
    const titles = Array.from(container.querySelectorAll('.stuck-card__title')).map((node) =>
      (node.textContent ?? '').trim()
    )
    expect(titles).toEqual(['循环', '数组'])

    const links = await findAllByText('查看原文')
    await fireEvent.click(links[0])
    expect(push).toHaveBeenCalledWith({
      name: 'workspace-topic',
      params: { projectId: 'project-1', topicId: 'topic-1' },
      query: { blockId: 'block-loop' },
    })
  })

  it('勾中的卡点带进提纲，讲次就是勾选顺序', async () => {
    const { container, findAllByText, findByText } = mount()

    await findAllByText('循环')
    const boxes = container.querySelectorAll<HTMLInputElement>('.stuck-card input[type="checkbox"]')
    expect(boxes.length).toBe(2)

    // Vuetify 把模型更新挂在原生 input 事件上（VSelectionControl.onInput）。
    boxes[0]!.checked = true
    await fireEvent.input(boxes[0]!)
    await findByText('已选 1 条发言')

    await fireEvent.click(await findByText('生成讲解提纲'))
    await waitFor(() => expect(buildLearningOutline).toHaveBeenCalledWith(42, { blockIds: ['block-loop'] }))

    await findByText('建议第 1 讲')
    await findByText('程序设计基础 · 共性问题讲解提纲')
  })

  it('提纲里读不到的发言照样说出来', async () => {
    buildLearningOutline.mockResolvedValue({
      data: { ...outlinePayload, sections: [], missing: ['block-gone'] },
    })

    const { container, findAllByText, findByText } = mount()

    await findAllByText('循环')
    const box = container.querySelectorAll<HTMLInputElement>('.stuck-card input[type="checkbox"]')[0]!
    box.checked = true
    await fireEvent.input(box)
    await fireEvent.click(await findByText('生成讲解提纲'))

    await findByText('有 1 条发言已经读不到，没有放进这份提纲')
    await findByText('暂无可讲解的内容')
  })
})

describe('筛选', () => {
  it('地址栏里的学生与知识点进到发言请求里，队列只收时间与学生', async () => {
    route.query = { student: 'zhangsan', knowledgePoint: '7' }
    mount()

    await waitFor(() =>
      expect(getLearningQuestions).toHaveBeenCalledWith(
        42,
        expect.objectContaining({ student: 'zhangsan', knowledgePoint: 7 })
      )
    )
    expect(getLearningQueues).toHaveBeenCalledWith(42, expect.not.objectContaining({ knowledgePoint: 7 }))
  })
})

describe('空态', () => {
  it('一个学生项目都读不到时，画的是空态而不是空列表', async () => {
    getLearningFilters.mockResolvedValue({ data: { students: [], knowledgePoints: [], projectCount: 0 } })
    getLearningQueues.mockResolvedValue({
      data: { reviewFlag: { available: false, reason: REVIEW_REASON, items: [] }, stuckPoints: [] },
    })
    getLearningQuestions.mockResolvedValue({ data: { questions: [], total: 0 } })

    const { findByText } = mount()

    await findByText('暂无可查看的学生项目')
  })

  it('筛掉全部发言时写「暂无学生发言」', async () => {
    getLearningQuestions.mockResolvedValue({ data: { questions: [], total: 0 } })

    const { findByText } = mount()

    await findByText('暂无学生发言')
  })
})
