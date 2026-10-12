import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent } from 'vue'
import { cleanup, render as renderComponent } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getLearningFilters: vi.fn(),
  getLearningQueues: vi.fn(),
  getLearningQuestions: vi.fn(),
  buildLearningOutline: vi.fn(),
}))
vi.mock('@/network/api/spaces', () => ({ SpacesApi: api }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

import Learning from './Learning.vue'

import i18n from '@/i18n'
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
  props: ['student', 'knowledgePoint', 'loading', 'outlineLoading', 'learningFilters', 'queues', 'questions', 'outline', 'failed', 'errorDetail', 'selected'],
  setup(props, { emit }) {
    state = props
    send = emit
    return () => null
  },
})
async function render(query = '') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/spaces/:spaceId/learning', component: Learning }] })
  await router.push(`/spaces/7/learning${query}`)
  await router.isReady()
  renderComponent(Learning, { global: { plugins: [router, i18n], stubs: { LearningView: Probe } } })
  await flushPromises()
  return { router }
}
beforeEach(() => {
  vi.resetAllMocks()
  api.getLearningFilters.mockResolvedValue({ data: LEARNING_FILTERS })
  api.getLearningQueues.mockResolvedValue({ data: LEARNING_QUEUES })
  api.getLearningQuestions.mockResolvedValue({ data: { questions: LEARNING_QUESTIONS } })
  api.buildLearningOutline.mockResolvedValue({ data: LEARNING_OUTLINE })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('学习看板容器', () => {
  it('队列和发言并发读取，任一尚未返回时保持 loading', async () => {
    const queues = deferred<{ data: typeof LEARNING_QUEUES }>()
    const questions = deferred<{ data: { questions: typeof LEARNING_QUESTIONS } }>()
    api.getLearningQueues.mockReturnValueOnce(queues.promise)
    api.getLearningQuestions.mockReturnValueOnce(questions.promise)
    await render()
    expect(api.getLearningQueues).toHaveBeenCalledOnce()
    expect(api.getLearningQuestions).toHaveBeenCalledOnce()
    expect(state['loading']).toBe(true)
    queues.resolve({ data: LEARNING_QUEUES })
    await flushPromises()
    expect(state['loading']).toBe(true)
    questions.resolve({ data: { questions: LEARNING_QUESTIONS } })
    await flushPromises()
    expect(state['loading']).toBe(false)
  })

  it('路由筛选映射到两个模型，模型变化写回路由且后退更新模型', async () => {
    const { router } = await render('?student=alice&knowledgePoint=7')
    expect(state['student']).toBe('alice')
    expect(state['knowledgePoint']).toBe(7)
    expect(api.getLearningQuestions).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ student: 'alice', knowledgePoint: 7 })
    )
    send('update:student', 'bob')
    send('update:knowledgePoint', null)
    await flushPromises()
    expect(router.currentRoute.value.query.student).toBe('bob')
    expect(router.currentRoute.value.query.knowledgePoint).toBeUndefined()
    await router.push({ query: { student: 'alice', knowledgePoint: '7' } })
    await flushPromises()
    expect(state['student']).toBe('alice')
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
    const refresh = deferred<{ data: typeof LEARNING_QUEUES }>()
    api.getLearningQueues.mockReturnValue(refresh.promise)
    await router.push({ query: { student: 'bob' } })
    await flushPromises()
    expect(state['loading']).toBe(true)
    expect(state['queues']).toEqual(LEARNING_QUEUES)
    expect(state['selected']).toEqual(['b-1', 'b-2'])
    expect(state['outline']).toEqual(LEARNING_OUTLINE)
    refresh.resolve({ data: LEARNING_QUEUES })
    await flushPromises()
    const outline = deferred<{ data: typeof LEARNING_OUTLINE }>()
    api.buildLearningOutline.mockReturnValueOnce(outline.promise)
    send('buildOutline')
    await flushPromises()
    expect(state['outlineLoading']).toBe(true)
    expect(state['outline']).toEqual(LEARNING_OUTLINE)
    outline.resolve({ data: LEARNING_OUTLINE })
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
})
