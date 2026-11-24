// 「新建任务」连点（或从几处入口各点一下）只建一件：前一件还在建的时候，再点不会多出
// 一件空任务；建好之后再点，照常再建一件。
import type { Router } from 'vue-router'
import type { Topic } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/** 服务器上建出来的任务；`hold` 着的时候，建的请求先不回。 */
const made: string[] = []
const waiting: (() => void)[] = []
const release = () => waiting.splice(0).forEach((resolve) => resolve())
let hold = false
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  createRoomTask: async (roomId: string) => {
    const id = `t-${made.length + 1}`
    made.push(`${roomId}/${id}`)
    if (hold) await new Promise<void>((resolve) => waiting.push(resolve))
    return { id }
  },
}))

import { creatingTaskIn, newTask } from './topicActions'

const TOPIC = { id: 'room-1', project_id: 'p-1' } as Topic
const router = { push: async () => {} } as unknown as Router

beforeEach(() => {
  setActivePinia(createPinia())
  made.length = 0
  hold = false
})

describe('新建任务', () => {
  it('前一件还在建：再点不会多建一件', async () => {
    hold = true
    const first = newTask(TOPIC, router)
    const second = newTask(TOPIC, router)
    release()
    await Promise.all([first, second])

    expect(made).toHaveLength(1)
  })

  it('建的这段时间里入口知道它在建，建好就放开', async () => {
    hold = true
    const first = newTask(TOPIC, router)
    expect(creatingTaskIn(TOPIC.id)).toBe(true)
    release()
    await first

    expect(creatingTaskIn(TOPIC.id)).toBe(false)
  })

  it('建好之后再点，照常再建一件', async () => {
    await newTask(TOPIC, router)
    await newTask(TOPIC, router)

    expect(made).toHaveLength(2)
  })
})
