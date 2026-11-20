// 「数据」页签的取数：测试点那一块只认最后一次问的回答。老师快速切阶段时，先问后到的
// 那个阶段的数不能盖掉后问的；换了一道题，上一道题的数当场清掉。
import { defineComponent, h, nextTick } from 'vue'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ stats: vi.fn() }))
vi.mock('@/api/taskJudge', () => ({ TaskJudgeApi: api }))
vi.mock('@/api/taskBoard', () => ({
  TaskBoardApi: { getParticipants: vi.fn().mockResolvedValue({ participants: [] }) },
}))
vi.mock('@/api/spaces', () => ({
  SpacesApi: { getSubmissionQueue: vi.fn().mockResolvedValue({ submissions: [] }) },
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { spaceId: '7' } }) }))

// 画面换成一个只把收到的数据摆出来、并能发「换阶段」的桩：这里量的是取数，不是画面。
vi.mock('./InsightsView.vue', () => ({
  default: defineComponent({
    props: { judgeStats: { type: Object, default: null } },
    emits: ['judge-stage', 'retry'],
    setup(props, { emit }) {
      return () =>
        h('div', [
          h('span', { 'data-testid': 'stage' }, String((props.judgeStats as { stage?: string } | null)?.stage ?? '')),
          h('button', { 'data-testid': 's1', onClick: () => emit('judge-stage', 's1') }),
          h('button', { 'data-testid': 's2', onClick: () => emit('judge-stage', 's2') }),
          h('button', { 'data-testid': 'retry', onClick: () => emit('retry') }),
        ])
    },
  }),
}))

import Insights from './Insights.vue'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

const task = (id: number) => ({ id }) as never

beforeEach(() => api.stats.mockReset())
afterEach(cleanup)

const flushPromises = () => new Promise((r) => setTimeout(r, 0))
const click = (id: string) => fireEvent.click(screen.getByTestId(id))
const stage = () => screen.getByTestId('stage').textContent

describe('测试点那一块只认最后一次问的回答', () => {
  it('先问的阶段后到，不会盖掉后问的阶段', async () => {
    api.stats.mockResolvedValueOnce({ stage: 's1' })
    render(Insights, { props: { taskData: task(42), isCreator: true } })
    await flushPromises()

    const slow = deferred<unknown>()
    const fast = deferred<unknown>()
    api.stats.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    await click('s1')
    await click('s2')
    fast.resolve({ stage: 's2' })
    await flushPromises()
    slow.resolve({ stage: 's1' })
    await flushPromises()

    expect(stage()).toBe('s2')
  })

  it('重试时沿用老师选的阶段，不回到第一个', async () => {
    api.stats.mockResolvedValue({ stage: 's2' })
    render(Insights, { props: { taskData: task(42), isCreator: true } })
    await flushPromises()
    await click('s2')
    await flushPromises()
    api.stats.mockClear()

    await click('retry')
    await flushPromises()
    expect(api.stats).toHaveBeenCalledWith(42, 's2')
  })

  it('选的阶段问不到，重试回到默认阶段', async () => {
    api.stats.mockResolvedValueOnce({ stage: 's1' })
    render(Insights, { props: { taskData: task(42), isCreator: true } })
    await flushPromises()
    api.stats.mockRejectedValueOnce(new Error('stageReferenceUnknown'))
    await click('s2')
    await flushPromises()
    api.stats.mockClear()
    api.stats.mockResolvedValueOnce({ stage: 's1' })

    await click('retry')
    await flushPromises()
    expect(api.stats).toHaveBeenCalledWith(42, null)
  })

  it('换了一道题，上一道题的数当场清掉', async () => {
    api.stats.mockResolvedValueOnce({ stage: 'old' })
    const view = render(Insights, { props: { taskData: task(42), isCreator: true } })
    await flushPromises()
    expect(stage()).toBe('old')

    api.stats.mockReturnValueOnce(new Promise(() => undefined))
    await view.rerender({ taskData: task(43), isCreator: true })
    await nextTick()
    expect(stage()).toBe('')
  })
})
