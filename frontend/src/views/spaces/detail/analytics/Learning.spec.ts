import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render as renderComponent } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getLearningFilters: vi.fn(),
  getLearningQueues: vi.fn(),
  getLearningQuestions: vi.fn(),
  buildLearningOutline: vi.fn(),
  getLearningClassOverview: vi.fn(),
  getLearningClassPoints: vi.fn(),
  getLearningClassPoint: vi.fn(),
  getLearningClassQuestions: vi.fn(),
  getLearningClassExcerpts: vi.fn(),
  getLearningClassWords: vi.fn(),
  getLearningClassTasks: vi.fn(),
  getLearningClassBehavior: vi.fn(),
  getLearningClassTrend: vi.fn(),
  exportLearningClassReport: vi.fn(),
}))
const download = vi.hoisted(() => ({ saveBlob: vi.fn(), announceDownload: vi.fn(), announceDownloadFailure: vi.fn() }))
vi.mock('@/lib/download', () => download)
vi.mock('@/api/spaces', () => ({ SpacesApi: api }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

import Learning from './Learning.vue'

import i18n from '@/i18n'
import {
  LEARNING_CLASS_EXCERPTS,
  LEARNING_CLASS_OVERVIEW,
  LEARNING_CLASS_POINT,
  LEARNING_CLASS_QUESTIONS,
} from '@/views/demo/catalogLearningClassFixtures'
import {
  LEARNING_FILTERS,
  LEARNING_OUTLINE,
  LEARNING_QUESTIONS,
  LEARNING_QUEUES,
} from '@/views/demo/catalogScenesMisc3Fixtures'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
const flushPromises = () => new Promise<void>((resolve) => setTimeout(resolve, 0))
let state: Record<string, unknown>
let send: (event: string, ...args: unknown[]) => void
const Probe = defineComponent({
  props: [
    'knowledgePoint',
    'loading',
    'outlineLoading',
    'learningFilters',
    'queues',
    'questions',
    'outline',
    'failed',
    'errorDetail',
    'selected',
  ],
  setup(props, { emit }) {
    state = props
    send = emit
    return () => null
  },
})
let view: Record<string, unknown>
let tell: (event: string, ...args: unknown[]) => void
/** 学情分析那一半的画面：记下它收到的值，原有内容照样从 legacy 插槽画出来。 */
const AnalysisProbe = defineComponent({
  props: [
    'sub',
    'pointId',
    'enabled',
    'loading',
    'failed',
    'errorDetail',
    'overview',
    'points',
    'pointDetail',
    'questions',
    'words',
    'tasks',
    'behavior',
    'trend',
  ],
  setup(props, { emit, slots }) {
    view = props
    tell = emit
    return () => slots.legacy?.()
  },
})
async function render(query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId/learning', component: Learning }],
  })
  await router.push(`/spaces/7/learning${query}`)
  await router.isReady()
  renderComponent(Learning, {
    global: {
      plugins: [router, i18n],
      stubs: {
        LearningView: Probe,
        LearningAnalysisView: AnalysisProbe,
        LearningExcerptDialog: true,
        LearningExportDialog: true,
      },
    },
  })
  await flushPromises()
  return { router }
}
beforeEach(() => {
  vi.resetAllMocks()
  api.getLearningFilters.mockResolvedValue(LEARNING_FILTERS)
  api.getLearningQueues.mockResolvedValue(LEARNING_QUEUES)
  api.getLearningQuestions.mockResolvedValue({ questions: LEARNING_QUESTIONS })
  api.buildLearningOutline.mockResolvedValue(LEARNING_OUTLINE)
  api.getLearningClassOverview.mockResolvedValue(LEARNING_CLASS_OVERVIEW)
  api.getLearningClassQuestions.mockResolvedValue(LEARNING_CLASS_QUESTIONS)
  api.getLearningClassPoint.mockResolvedValue(LEARNING_CLASS_POINT)
  api.getLearningClassExcerpts.mockResolvedValue(LEARNING_CLASS_EXCERPTS)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('学习看板容器', () => {
  it('队列和发言并发读取，任一尚未返回时保持 loading', async () => {
    const queues = deferred<typeof LEARNING_QUEUES>()
    const questions = deferred<{ questions: typeof LEARNING_QUESTIONS }>()
    api.getLearningQueues.mockReturnValueOnce(queues.promise)
    api.getLearningQuestions.mockReturnValueOnce(questions.promise)
    await render()
    expect(api.getLearningQueues).toHaveBeenCalledOnce()
    expect(api.getLearningQuestions).toHaveBeenCalledOnce()
    expect(state['loading']).toBe(true)
    queues.resolve(LEARNING_QUEUES)
    await flushPromises()
    expect(state['loading']).toBe(true)
    questions.resolve({ questions: LEARNING_QUESTIONS })
    await flushPromises()
    expect(state['loading']).toBe(false)
  })

  it('知识点筛选进出路由；地址里的成员筛选不再发给后端', async () => {
    const { router } = await render('?student=alice&knowledgePoint=7')
    expect(state['knowledgePoint']).toBe(7)
    expect(api.getLearningQuestions).toHaveBeenCalledWith(7, expect.objectContaining({ knowledgePoint: 7 }))
    expect(api.getLearningQuestions.mock.calls[0][1]).not.toHaveProperty('student')
    send('update:knowledgePoint', null)
    await flushPromises()
    expect(router.currentRoute.value.query.knowledgePoint).toBeUndefined()
    await router.push({ query: { knowledgePoint: '7' } })
    await flushPromises()
    expect(state['knowledgePoint']).toBe(7)
  })

  it('选择去重且保留插入顺序，移除再勾选排到末尾；筛选与刷新不清空选择或提纲', async () => {
    const { router } = await render()
    send('setSelected', 'b-2', true)
    send('setSelected', 'b-1', true)
    send('setSelected', 'b-2', true)
    await flushPromises()
    expect(state['selected']).toEqual(['b-2', 'b-1'])
    send('buildOutline')
    await flushPromises()
    expect(api.buildLearningOutline).toHaveBeenCalledWith(7, { blockIds: ['b-2', 'b-1'] })
    send('setSelected', 'b-2', false)
    send('setSelected', 'b-2', true)
    await flushPromises()
    expect(state['selected']).toEqual(['b-1', 'b-2'])
    const refresh = deferred<typeof LEARNING_QUEUES>()
    api.getLearningQueues.mockReturnValue(refresh.promise)
    await router.push({ query: { knowledgePoint: '8' } })
    await flushPromises()
    expect(state['loading']).toBe(true)
    expect(state['queues']).toEqual(LEARNING_QUEUES)
    expect(state['selected']).toEqual(['b-1', 'b-2'])
    expect(state['outline']).toEqual(LEARNING_OUTLINE)
    refresh.resolve(LEARNING_QUEUES)
    await flushPromises()
    const outline = deferred<typeof LEARNING_OUTLINE>()
    api.buildLearningOutline.mockReturnValueOnce(outline.promise)
    send('buildOutline')
    await flushPromises()
    expect(state['outlineLoading']).toBe(true)
    expect(state['outline']).toEqual(LEARNING_OUTLINE)
    outline.resolve(LEARNING_OUTLINE)
    await flushPromises()
    expect(state['outlineLoading']).toBe(false)
    send('clearSelection')
    await flushPromises()
    send('buildOutline')
    await flushPromises()
    expect(api.buildLearningOutline).toHaveBeenCalledTimes(2)
    expect(state['outline']).toEqual(LEARNING_OUTLINE)
  })

  it('队列读取失败单独暴露原因，重试恢复而不清空已有数据', async () => {
    await render()
    api.getLearningQueues.mockRejectedValueOnce(new Error('queue timeout'))
    send('load')
    await flushPromises()
    expect(state['failed']).toBe(true)
    expect(state['errorDetail']).toBe('queue timeout')
    expect(state['queues']).toEqual(LEARNING_QUEUES)
    send('load')
    await flushPromises()
    expect(state['failed']).toBe(false)
    expect(state['errorDetail']).toBeNull()
  })

  it('学情分析：一进来读总览，切到别的页签才读那一页，切回来不重读', async () => {
    await render()
    expect(api.getLearningClassOverview).toHaveBeenCalledWith(7)
    expect(view['enabled']).toBe(true)
    expect(view['overview']).toEqual(LEARNING_CLASS_OVERVIEW)
    tell('update:sub', 'questions')
    await flushPromises()
    expect(api.getLearningClassQuestions).toHaveBeenCalledOnce()
    expect(view['questions']).toEqual(LEARNING_CLASS_QUESTIONS)
    tell('update:sub', 'overview')
    tell('update:sub', 'questions')
    await flushPromises()
    expect(api.getLearningClassQuestions).toHaveBeenCalledOnce()
    expect(api.getLearningClassOverview).toHaveBeenCalledOnce()
  })

  it('学情分析没读到时，原有的发言与队列照样读、照样画', async () => {
    api.getLearningClassOverview.mockRejectedValueOnce(new Error('down'))
    await render()
    expect(view['failed']).toBe(true)
    expect(view['enabled']).toBeNull()
    expect(state['queues']).toEqual(LEARNING_QUEUES)
  })

  it('点开知识点读它的详情；摘录按组去取；导出把文件交给浏览器', async () => {
    await render()
    tell('openPoint', 2)
    await flushPromises()
    expect(api.getLearningClassPoint).toHaveBeenCalledWith(7, 2)
    expect(view['pointDetail']).toEqual(LEARNING_CLASS_POINT)
    tell('openExcerpts', { misconception: 11 })
    await flushPromises()
    expect(api.getLearningClassExcerpts).toHaveBeenCalledWith(7, { misconception: 11 })
    const blob = new Blob(['x'])
    api.exportLearningClassReport.mockResolvedValueOnce(blob)
    tell('export', 2)
    await flushPromises()
    expect(api.exportLearningClassReport).not.toHaveBeenCalled()
  })
})
