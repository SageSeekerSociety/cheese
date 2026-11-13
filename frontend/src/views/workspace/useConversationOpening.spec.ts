/** 在频道主线上转为任务，人留在频道里；在支线里转的，去那件任务的任务页（侧栏任务行点开的
 *  同一处），支线已经开着、转的就是它时也去；点开一件任务、或一条已经原地转成任务的支线，
 *  也是任务页。 */
import type { Block, RoomTask } from '@/cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const upgradeMessage = vi.fn()
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ upgradeMessage, reportError: vi.fn() }),
}))
vi.mock('@/api/threads', () => ({ openThread: vi.fn() }))

import { useConversationOpening } from './useConversationOpening'

const blank = { template: '<div />' }

function setup() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/p/:projectId/c/:topicId', name: 'workspace-topic', component: blank },
      { path: '/p/:projectId/c/:topicId/t/:threadId', name: 'workspace-thread', component: blank },
      { path: '/p/:projectId/tasks/:taskId', name: 'workspace-task', component: blank },
    ],
  })
  const place = () => {
    const { projectId, topicId, taskId, threadId } = router.currentRoute.value.params as Record<string, string>
    return { projectId, topicId: topicId ?? 'r1', taskId, threadId }
  }
  const opening = useConversationOpening({ router, place, markSeen: () => {} })
  return { router, opening }
}

function made(id: string): RoomTask {
  return { id, room_id: 'r1', title: '登录验证码', status: 'open' } as RoomTask
}

beforeEach(() => upgradeMessage.mockReset())

describe('转为任务之后', () => {
  it('从频道主线转：人留在频道里，不去任务页', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/c/r1')
    upgradeMessage.mockResolvedValue(made('th1'))
    await opening.upgradeInColumn('b1')
    expect(upgradeMessage).toHaveBeenCalledWith('b1')
    expect(router.currentRoute.value.name).toBe('workspace-topic')
  })

  it('右半边开着一条支线时，从主线上转另一条消息：人也留在频道里', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/c/r1/t/th0')
    upgradeMessage.mockResolvedValue(made('th1'))
    await opening.upgradeInColumn('b1')
    expect(router.currentRoute.value.name).toBe('workspace-thread')
  })

  it('任务页上的一条消息另开一件：去那件的任务页', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/tasks/t0')
    upgradeMessage.mockResolvedValue(made('t1'))
    await opening.upgradeInColumn('b1')
    expect(router.currentRoute.value.name).toBe('workspace-task')
    expect(router.currentRoute.value.params.taskId).toBe('t1')
  })

  it('支线里的一条回复另开一件：去这件任务的任务页', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/c/r1/t/th0')
    upgradeMessage.mockResolvedValue(made('t1'))
    await opening.upgradeMessage('b1')
    expect(router.currentRoute.value.name).toBe('workspace-task')
    expect(router.currentRoute.value.params.taskId).toBe('t1')
  })

  it('这条支线已经开着、原地转成了任务：也去任务页，退回去回到频道而不是那条支线', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/c/r1')
    await router.push('/p/p1/c/r1/t/th1')
    upgradeMessage.mockResolvedValue(made('th1'))
    await opening.upgradeMessage('b1')
    expect(router.currentRoute.value.name).toBe('workspace-task')
    expect(router.currentRoute.value.params.taskId).toBe('th1')
    router.back()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-topic'))
  })

  it('没转成：哪也不去', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/c/r1/t/th1')
    upgradeMessage.mockResolvedValue(null)
    await opening.upgradeMessage('b1')
    expect(router.currentRoute.value.name).toBe('workspace-thread')
  })
})

describe('点开一件任务', () => {
  it('主线上一条消息的支线已经原地转成任务：去任务页，不在右半边当支线开', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/c/r1')
    const block = {
      id: 'b1',
      thread: { id: 'th1', tasks: [{ id: 'th1', title: '登录验证码', status: 'open' }] },
    } as unknown as Block
    await opening.onOpenThread(block)
    expect(router.currentRoute.value.name).toBe('workspace-task')
    expect(router.currentRoute.value.params.taskId).toBe('th1')
  })

  it('还没转的支线照旧在右半边打开', async () => {
    const { router, opening } = setup()
    await router.push('/p/p1/c/r1')
    const block = { id: 'b1', thread: { id: 'th1', tasks: [] } } as unknown as Block
    await opening.onOpenThread(block)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-thread'))
  })
})
