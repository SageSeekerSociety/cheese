// 「领取之前那份清单」的取数那一半 (#944)。
//
// 这一条盯的是切题：一道题的清单还在路上时人换了另一道题，**新题必须自己去取**，
// 而且那个晚到的旧响应不许再落到屏幕上。
//
// 旧实现在这里栽了两次：闸门写成 `if (loading.value || loadedFor.value === id)`，
// 于是「在途」这件事被当成了「在途的那一道题」—— 切题时新题一个请求都发不出去，
// 屏幕上一直停着旧题那份清单；就算发了，晚到的旧响应也照样把新题的覆盖掉。
import type { TaskInheritanceData } from '@/network/api/tasks/types'

import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ inheritance: vi.fn() }))

vi.mock('@/network/api/tasks', () => ({ TasksApi: { inheritance: mocks.inheritance } }))

import { useTaskInheritance } from './useTaskInheritance'

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function payload(taskId: number): TaskInheritanceData {
  return {
    taskId,
    spaceId: 1,
    resourcePack: { compute_credits: taskId * 100 },
    teaching: {
      systemPrompt: null,
      currentWeek: null,
      allowedTopics: [],
      avoidInCode: [],
      materialIds: [],
      knowledgeIds: [],
      source: null,
    },
    materials: [],
  }
}

function deferred() {
  let resolve!: (value: { data: TaskInheritanceData }) => void
  const promise = new Promise<{ data: TaskInheritanceData }>((r) => (resolve = r))
  return { promise, resolve }
}

describe('会继承什么：取数那一半', () => {
  beforeEach(() => {
    mocks.inheritance.mockReset()
  })

  it('在途时换了一道题：新题自己去取，晚到的旧响应不许落到屏幕上', async () => {
    const first = deferred()
    const second = deferred()
    mocks.inheritance.mockImplementation((id: number) => (id === 1 ? first.promise : second.promise))

    const taskId = ref<number | null>(1)
    const { inheritance } = useTaskInheritance(taskId)
    expect(mocks.inheritance).toHaveBeenCalledWith(1)

    // 第一道题还没回来，人已经换了第二道题。
    taskId.value = 2
    await nextTick()
    expect(mocks.inheritance).toHaveBeenCalledWith(2)

    // 旧的先回来、新的后回来 —— 屏幕上必须是第二道题的。
    first.resolve({ data: payload(1) })
    second.resolve({ data: payload(2) })
    await flush()

    expect(inheritance.value?.taskId).toBe(2)
    expect(inheritance.value?.resourcePack).toEqual({ compute_credits: 200 })
  })

  it('在途时被清空：晚到的旧响应不许把清单写回来', async () => {
    const first = deferred()
    mocks.inheritance.mockImplementation(() => first.promise)

    const taskId = ref<number | null>(1)
    const { inheritance } = useTaskInheritance(taskId)
    expect(mocks.inheritance).toHaveBeenCalledWith(1)

    // 还没回来，题已经空了（对话框关掉、题被撤下）。
    taskId.value = null
    await nextTick()

    first.resolve({ data: payload(1) })
    await flush()

    expect(inheritance.value).toBeNull()
  })

  it('同一道题不重复取', async () => {
    mocks.inheritance.mockResolvedValue({ data: payload(7) })
    const taskId = ref<number | null>(7)
    const { inheritance, reload } = useTaskInheritance(taskId)
    await flush()

    await reload()

    expect(mocks.inheritance).toHaveBeenCalledTimes(1)
    expect(inheritance.value?.taskId).toBe(7)
  })

  it('取不到就空着，不拦人', async () => {
    mocks.inheritance.mockRejectedValue(new Error('HTTP 404'))
    const taskId = ref<number | null>(9)
    const { inheritance, loading } = useTaskInheritance(taskId)
    await flush()

    expect(inheritance.value).toBeNull()
    expect(loading.value).toBe(false)
  })

  it('还没有题目 id 就不问', async () => {
    const taskId = ref<number | null>(null)
    useTaskInheritance(taskId)
    await flush()

    expect(mocks.inheritance).not.toHaveBeenCalled()
  })
})
