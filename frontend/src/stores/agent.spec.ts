// 交给芝士的文件：先传上去，传完那一行不再转圈，下一个问题带上它。
import { afterEach, describe, expect, it, vi } from 'vitest'

const upload = vi.fn()
vi.mock('@/api/attachments', () => ({ AttachmentsApi: { upload: (...a: unknown[]) => upload(...a) } }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

import { agentFiles, attachToAgent, removeAgentFile, resetAgent } from './agent'

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
