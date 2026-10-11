// 上手三步的事实问服务端；房间里来了新消息就再问一次，不在项目本体上就不问。
import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getStartSteps = vi.fn()
vi.mock('@/api', () => ({ getStartSteps: (id: string) => getStartSteps(id) }))

import { useStartSteps } from './useStartSteps'

const flush = async () => {
  for (let i = 0; i < 3; i += 1) await nextTick()
}

beforeEach(() => {
  getStartSteps.mockReset().mockResolvedValue({ asked: false, answered: false, accepted: false, learned: false })
})

describe('useStartSteps', () => {
  it('asks again when a message arrives in the room', async () => {
    const messages = ref(0)
    const steps = useStartSteps(
      () => 'p1',
      () => messages.value
    )
    await flush()
    expect(steps.value?.asked).toBe(false)
    getStartSteps.mockResolvedValue({ asked: true, answered: false, accepted: false, learned: false })
    messages.value = 1
    await flush()
    expect(steps.value?.asked).toBe(true)
    expect(getStartSteps).toHaveBeenCalledTimes(2)
  })

  it('does not ask outside a project line', async () => {
    const steps = useStartSteps(
      () => null,
      () => 0
    )
    await flush()
    expect(getStartSteps).not.toHaveBeenCalled()
    expect(steps.value).toBeNull()
  })
})
