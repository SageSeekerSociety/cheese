// 面板上「正在等的事」：只列这段对话里还在等的那几件；一轮回答结束后重读（这一轮可能刚建了
// 一条）；取消当场拿掉，没取消成就回来。
import type { AssistantRoutine } from '@/api'

import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', () => ({
  getAssistantRoutines: vi.fn(),
  setAssistantRoutine: vi.fn(),
}))

import { useAgentWaits } from './useAgentWaits'

import { getAssistantRoutines, setAssistantRoutine } from '@/api'

function rule(id: string, fields: Partial<AssistantRoutine> = {}): AssistantRoutine {
  return {
    id,
    title: id,
    note: '',
    when: '任务「接入搜索」提交审阅时',
    state: 'active',
    paused: '',
    nextRunAt: null,
    once: true,
    conversationId: 'here',
    lastRun: null,
    weekCredits: 0,
    ...fields,
  }
}

const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0))

const listed = (routines: AssistantRoutine[]) => ({ routines, limit: 20 })

describe('useAgentWaits', () => {
  beforeEach(() => vi.resetAllMocks())

  it('lists only what this conversation is still waiting for', async () => {
    vi.mocked(getAssistantRoutines).mockResolvedValue(
      listed([
        rule('waiting'),
        rule('elsewhere', { conversationId: 'there' }),
        rule('repeats', { once: false }),
        rule('happened', { state: 'done' }),
      ])
    )
    const { waits } = useAgentWaits(ref('here'), ref(false))
    await flushPromises()

    expect(waits.value.map((w) => w.id)).toEqual(['waiting'])
  })

  it('reads again once an answer ends, which may have made one', async () => {
    vi.mocked(getAssistantRoutines).mockResolvedValueOnce(listed([]))
    const busy = ref(true)
    const { waits } = useAgentWaits(ref('here'), busy)
    await flushPromises()
    vi.mocked(getAssistantRoutines).mockResolvedValueOnce(listed([rule('new')]))

    busy.value = false
    await flushPromises()

    expect(waits.value.map((w) => w.id)).toEqual(['new'])
  })

  it('takes a cancelled one away, and puts it back when the cancel fails', async () => {
    vi.mocked(getAssistantRoutines).mockResolvedValue(listed([rule('waiting')]))
    vi.mocked(setAssistantRoutine).mockRejectedValue(new Error('offline'))
    const { waits, cancel } = useAgentWaits(ref('here'), ref(false))
    await flushPromises()

    const done = cancel('waiting')
    expect(waits.value).toEqual([])
    expect(await done).toBe(false)
    expect(waits.value.map((w) => w.id)).toEqual(['waiting'])
  })
})
