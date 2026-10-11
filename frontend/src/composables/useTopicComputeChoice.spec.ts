// 房间换电脑那一项的动作：点一下发一次请求，三种回执分别落成什么样。
import type { ComputeChoice } from '@/types/compute'

import { defineComponent, h } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const setTopicComputeChoice = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    setTopicComputeChoice: (...a: unknown[]) => setTopicComputeChoice(...a),
  }
})

import { useTopicComputeChoice } from './useTopicComputeChoice'

const cloud: ComputeChoice = { name: null, profile: 'cloud', device_id: null }
const lab: ComputeChoice = { ...cloud, name: '实验室工作站', profile: 'device', device_id: 'office' }

function host(topicId: () => string = () => 't1', onChanged?: () => void) {
  let state!: ReturnType<typeof useTopicComputeChoice>
  render(
    defineComponent({
      setup() {
        state = useTopicComputeChoice(topicId, { onChanged })
        return () => h('div')
      },
    })
  )
  return state
}

beforeEach(() => {
  setTopicComputeChoice.mockReset().mockResolvedValue({ choice: lab, proposal: null })
})
afterEach(cleanup)

describe('useTopicComputeChoice', () => {
  it('sends the pick up and bumps the receipt when it sticks', async () => {
    const onChanged = vi.fn()
    const state = host(() => 't1', onChanged)
    await state.select(lab)
    expect(setTopicComputeChoice).toHaveBeenCalledWith('t1', lab, {})
    expect(state.savedVersion.value).toBe(1)
    expect(onChanged).toHaveBeenCalledTimes(1)
    expect(state.error.value).toBe('')
    expect(state.proposal.value).toBe('')
  })

  it('carries the room access choice up as visibility', async () => {
    const state = host()
    await state.select(lab, 'host')
    expect(setTopicComputeChoice).toHaveBeenCalledWith('t1', lab, { visibility: 'host' })
  })

  it('reads the topic id lazily, so a task page can hand it its own id', async () => {
    let id = 'a'
    const state = host(() => id)
    await state.select(lab)
    id = 'b'
    await state.select(lab)
    expect(setTopicComputeChoice).toHaveBeenNthCalledWith(1, 'a', lab, {})
    expect(setTopicComputeChoice).toHaveBeenNthCalledWith(2, 'b', lab, {})
  })

  it('a proposal is not a save: no receipt, no refresh, the words are kept', async () => {
    setTopicComputeChoice.mockResolvedValue({
      choice: cloud,
      proposal: { approver: 'andyl', tier: 'byo', content: '这一步等 @andyl 点头。' },
    })
    const onChanged = vi.fn()
    const state = host(() => 't1', onChanged)
    await state.select(lab)
    expect(state.proposal.value).toBe('这一步等 @andyl 点头。')
    expect(state.savedVersion.value).toBe(0)
    expect(onChanged).not.toHaveBeenCalled()
    expect(state.error.value).toBe('')
  })

  it('keeps the reason when the switch is refused, and does not refresh', async () => {
    setTopicComputeChoice.mockRejectedValueOnce(new Error('正在准备环境，稍后再换'))
    const onChanged = vi.fn()
    const state = host(() => 't1', onChanged)
    await state.select(lab)
    expect(state.error.value).toBe('正在准备环境，稍后再换')
    expect(state.savedVersion.value).toBe(0)
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('flags saving while a switch is on its way', async () => {
    let release!: (value: unknown) => void
    setTopicComputeChoice.mockImplementation(() => new Promise((r) => (release = r)))
    const state = host()
    const pending = state.select(lab)
    expect(state.saving.value).toBe(true)
    release({ choice: lab, proposal: null })
    await pending
    expect(state.saving.value).toBe(false)
  })

  it('ignores a second pick while one is still in flight', async () => {
    let release!: (value: unknown) => void
    setTopicComputeChoice.mockImplementation(() => new Promise((r) => (release = r)))
    const state = host()
    const first = state.select(lab)
    await state.select(cloud)
    expect(setTopicComputeChoice).toHaveBeenCalledTimes(1)
    release({ choice: lab, proposal: null })
    await first
  })
})
