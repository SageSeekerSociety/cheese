import { createMemoryHistory, createRouter } from 'vue-router'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getLearningFilters: vi.fn(), getLearningQueues: vi.fn(), getLearningQuestions: vi.fn(), buildLearningOutline: vi.fn(),
}))
vi.mock('@/network/api/spaces', () => ({ SpacesApi: api }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

import Learning from './Learning.vue'
import LearningView from './LearningView.vue'

import i18n from '@/i18n'
import { LEARNING_FILTERS, LEARNING_OUTLINE, LEARNING_QUESTIONS, LEARNING_QUEUES } from '@/views/demo/catalogScenesMisc3Fixtures'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
const mounted: { unmount: () => void }[] = []
async function render(query = '') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/spaces/:spaceId/learning', component: Learning }] })
  await router.push(`/spaces/7/learning${query}`)
  await router.isReady()
  const wrapper = shallowMount(Learning, { global: { plugins: [router, i18n] } })
  mounted.push(wrapper)
  await flushPromises()
  return { view: wrapper.findComponent(LearningView), router }
}
beforeEach(() => {
  vi.resetAllMocks()
  api.getLearningFilters.mockResolvedValue({ data: LEARNING_FILTERS })
  api.getLearningQueues.mockResolvedValue({ data: LEARNING_QUEUES })
  api.getLearningQuestions.mockResolvedValue({ data: { questions: LEARNING_QUESTIONS } })
  api.buildLearningOutline.mockResolvedValue({ data: LEARNING_OUTLINE })
})
afterEach(() => {
  mounted.splice(0).forEach((view) => view.unmount())
  vi.restoreAllMocks()
})

describe('学习看板容器', () => {
  it('队列和发言并发读取，任一尚未返回时保持 loading', async () => {
    const queues = deferred<{ data: typeof LEARNING_QUEUES }>()
    const questions = deferred<{ data: { questions: typeof LEARNING_QUESTIONS } }>()
    api.getLearningQueues.mockReturnValueOnce(queues.promise)
    api.getLearningQuestions.mockReturnValueOnce(questions.promise)
    const { view } = await render()
    expect(api.getLearningQueues).toHaveBeenCalledOnce()
    expect(api.getLearningQuestions).toHaveBeenCalledOnce()
    expect(view.props('loading')).toBe(true)
    queues.resolve({ data: LEARNING_QUEUES })
    await flushPromises()
    expect(view.props('loading')).toBe(true)
    questions.resolve({ data: { questions: LEARNING_QUESTIONS } })
    await flushPromises()
    expect(view.props('loading')).toBe(false)
  })

  it('路由筛选映射到两个模型，模型变化写回路由且后退更新模型', async () => {
    const { view, router } = await render('?student=alice&knowledgePoint=7')
    expect(view.props('student')).toBe('alice')
    expect(view.props('knowledgePoint')).toBe(7)
    expect(api.getLearningQueues).toHaveBeenCalledWith(7, expect.objectContaining({ student: 'alice', knowledgePoint: 7 }))
    view.vm.$emit('update:student', 'bob')
    view.vm.$emit('update:knowledgePoint', null)
    await flushPromises()
    expect(router.currentRoute.value.query.student).toBe('bob')
    expect(router.currentRoute.value.query.knowledgePoint).toBeUndefined()
    await router.push({ query: { student: 'alice', knowledgePoint: '7' } })
    await flushPromises()
    expect(view.props('student')).toBe('alice')
    expect(view.props('knowledgePoint')).toBe(7)
  })

  it('选择去重且保留插入顺序，移除再勾选排到末尾；筛选与刷新不清空选择或提纲', async () => {
    const { view, router } = await render()
    view.vm.$emit('setSelected', 'b-2', true)
    view.vm.$emit('setSelected', 'b-1', true)
    view.vm.$emit('setSelected', 'b-2', true)
    await flushPromises()
    expect(view.props('selected')).toEqual(['b-2', 'b-1'])
    view.vm.$emit('buildOutline')
    await flushPromises()
    expect(api.buildLearningOutline).toHaveBeenCalledWith(7, { blockIds: ['b-2', 'b-1'] })
    view.vm.$emit('setSelected', 'b-2', false)
    view.vm.$emit('setSelected', 'b-2', true)
    await flushPromises()
    expect(view.props('selected')).toEqual(['b-1', 'b-2'])
    const refresh = deferred<{ data: typeof LEARNING_QUEUES }>()
    api.getLearningQueues.mockReturnValueOnce(refresh.promise)
    await router.push({ query: { student: 'bob' } })
    await flushPromises()
    expect(view.props('loading')).toBe(true)
    expect(view.props('queues')).toEqual(LEARNING_QUEUES)
    expect(view.props('selected')).toEqual(['b-1', 'b-2'])
    expect(view.props('outline')).toEqual(LEARNING_OUTLINE)
    refresh.resolve({ data: LEARNING_QUEUES })
    await flushPromises()
    const outline = deferred<{ data: typeof LEARNING_OUTLINE }>()
    api.buildLearningOutline.mockReturnValueOnce(outline.promise)
    view.vm.$emit('buildOutline')
    await flushPromises()
    expect(view.props('outlineLoading')).toBe(true)
    expect(view.props('outline')).toEqual(LEARNING_OUTLINE)
    outline.resolve({ data: LEARNING_OUTLINE })
    await flushPromises()
    expect(view.props('outlineLoading')).toBe(false)
    view.vm.$emit('clearSelection')
    await flushPromises()
    view.vm.$emit('buildOutline')
    await flushPromises()
    expect(api.buildLearningOutline).toHaveBeenCalledTimes(2)
    expect(view.props('outline')).toEqual(LEARNING_OUTLINE)
  })

  it('队列读取失败单独暴露原因，重试恢复而不清空已有数据', async () => {
    const { view } = await render()
    api.getLearningQueues.mockRejectedValueOnce(new Error('queue timeout'))
    view.vm.$emit('load')
    await flushPromises()
    expect(view.props('failed')).toBe(true)
    expect(view.props('errorDetail')).toBe('queue timeout')
    expect(view.props('queues')).toEqual(LEARNING_QUEUES)
    view.vm.$emit('load')
    await flushPromises()
    expect(view.props('failed')).toBe(false)
    expect(view.props('errorDetail')).toBeNull()
  })
})
