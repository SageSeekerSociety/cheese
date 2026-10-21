// 交给芝士的文件：先传上去，传完那一行不再转圈，下一个问题带上它。
import { afterEach, describe, expect, it, vi } from 'vitest'

const upload = vi.fn()
const post = vi.fn()
const openTask = vi.fn()
const settle = vi.fn()
vi.mock('@/api/attachments', () => ({ AttachmentsApi: { upload: (...a: unknown[]) => upload(...a) } }))
vi.mock('@/api/messages', () => ({ postChatMessage: (...a: unknown[]) => post(...a) }))
vi.mock('@/api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  createRoomTask: (...a: unknown[]) => openTask(...a),
  AgentProposalsApi: { settle: (...a: unknown[]) => settle(...a) },
}))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

import type { AssistantProposal } from './agentThread'

import { agentFiles, agentThread, attachToAgent, removeAgentFile, resetAgent, sendProposal } from './agent'

describe('交给芝士的文件', () => {
  afterEach(() => {
    resetAgent()
    upload.mockReset()
  })

  it('传完的那一行带上编号，不再是上传中', async () => {
    upload.mockResolvedValue({ id: 7 })

    await attachToAgent([new File(['x'], '实验3-缓存实验.pdf', { type: 'application/pdf' })])

    expect(agentFiles.value).toEqual([{ id: 7, name: '实验3-缓存实验.pdf', mime: 'application/pdf', uploading: false }])
  })

  it('传不上去的那一行拿掉', async () => {
    upload.mockRejectedValue(new Error('offline'))

    await attachToAgent([new File(['x'], 'a.pdf', { type: 'application/pdf' })])

    expect(agentFiles.value).toEqual([])
  })

  it('点掉一个文件', async () => {
    upload.mockResolvedValueOnce({ id: 1 }).mockResolvedValueOnce({ id: 2 })
    await attachToAgent([new File(['x'], 'a.pdf'), new File(['y'], 'b.pdf')])

    removeAgentFile(0)

    expect(agentFiles.value.map((f) => f.id)).toEqual([2])
  })
})

describe('芝士起草、人确认的事', () => {
  afterEach(() => {
    resetAgent()
    post.mockReset()
    openTask.mockReset()
    settle.mockReset()
  })

  const drafted = (p: Partial<AssistantProposal>): AssistantProposal => ({
    id: 5,
    kind: 'message',
    state: 'pending',
    request: 'req-5',
    project: 'p-1',
    ...p,
  })

  it('a message is posted as the person with the request it was drafted with, then marked sent', async () => {
    agentThread.proposals.value = [drafted({ topic: 'room-1', topicName: '综合', text: '原稿' })]
    post.mockResolvedValue({ id: 'block-1' })
    settle.mockResolvedValue({})

    await sendProposal(5, { text: '改过的' })

    expect(post).toHaveBeenCalledWith('room-1', { content: '改过的', request_id: 'req-5' })
    expect(settle).toHaveBeenCalledWith(5, 'sent', { topic: 'room-1', message: 'block-1' })
    expect(agentThread.proposals.value[0].state).toBe('sent')
  })

  it('a task is opened in its channel and its brief said in it, once even if sending is tried again', async () => {
    agentThread.proposals.value = [drafted({ kind: 'task', channel: 'room-1', title: '排查', brief: '复现' })]
    openTask.mockResolvedValue({ id: 'task-1' })
    post.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ id: 'block-2' })
    settle.mockResolvedValue({})

    await sendProposal(5, { title: '排查超时', text: '复现并修好' })
    expect(agentThread.proposals.value[0].state).toBe('pending')
    await sendProposal(5, { title: '排查超时', text: '复现并修好' })

    expect(openTask).toHaveBeenCalledTimes(1)
    expect(openTask).toHaveBeenCalledWith('room-1', '排查超时')
    expect(post).toHaveBeenLastCalledWith('task-1', { content: '复现并修好', request_id: 'req-5' })
    expect(settle).toHaveBeenCalledWith(5, 'sent', { task: 'task-1', topic: 'task-1', message: 'block-2' })
  })
})
