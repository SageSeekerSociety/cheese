import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  listTaskAttachments: vi.fn(),
  downloadFile: vi.fn().mockResolvedValue(undefined),
  taskAttachmentRawUrl: vi.fn(
    (taskId: number, attachmentId: number) => `/api/tasks/${taskId}/attachments/${attachmentId}/download`
  ),
}))
vi.mock('@/api/taskAttachments', () => ({ listTaskAttachments: mocks.listTaskAttachments }))
vi.mock('@/api', () => ({
  downloadFile: mocks.downloadFile,
  taskAttachmentRawUrl: mocks.taskAttachmentRawUrl,
}))

import { useTaskAttachments } from './useTaskAttachments'

const file = () => ({
  id: 3,
  name: '讲义.pdf',
  size: 2048,
  contentType: 'application/pdf',
  uploaderId: 1,
  downloadCount: 0,
  createdAt: 0,
})

describe('一道题的材料', () => {
  beforeEach(() => {
    mocks.listTaskAttachments.mockReset()
    mocks.downloadFile.mockClear()
  })

  it('清单和能不能下载都照服务端给的', async () => {
    mocks.listTaskAttachments.mockResolvedValue({ attachments: [file()], canDownload: false })
    const a = useTaskAttachments()

    await a.load(12)
    expect(mocks.listTaskAttachments).toHaveBeenCalledWith(12)
    expect(a.attachments.value.map((f) => f.name)).toEqual(['讲义.pdf'])
    expect(a.canDownload.value).toBe(false)
  })

  it('清单取不到时什么都不给，也不给下载', async () => {
    mocks.listTaskAttachments.mockRejectedValue(new Error('HTTP 500'))
    const a = useTaskAttachments()

    await a.load(12)
    expect(a.attachments.value).toEqual([])
    expect(a.canDownload.value).toBe(false)
  })

  it('已有清单但再次读取被拒绝时会清空旧清单', async () => {
    mocks.listTaskAttachments.mockResolvedValueOnce({ attachments: [file()], canDownload: true })
    const a = useTaskAttachments()
    await a.load(12)

    mocks.listTaskAttachments.mockRejectedValueOnce(new Error('HTTP 403'))
    await a.load(12)

    expect(a.attachments.value).toEqual([])
    expect(a.canDownload.value).toBe(false)
  })

  it('还没有题目 id 就不问', async () => {
    const a = useTaskAttachments()

    await a.load(null)
    expect(mocks.listTaskAttachments).not.toHaveBeenCalled()
  })

  it('下载走的是那道带鉴权的门，计数跟着涨一格', async () => {
    mocks.listTaskAttachments.mockResolvedValue({ attachments: [file()], canDownload: true })
    const a = useTaskAttachments()
    await a.load(12)

    await a.download(12, a.attachments.value[0])
    expect(mocks.taskAttachmentRawUrl).toHaveBeenCalledWith(12, 3)
    expect(mocks.downloadFile).toHaveBeenCalledWith('/api/tasks/12/attachments/3/download', '讲义.pdf')
    expect(a.attachments.value[0].downloadCount).toBe(1)
  })
})
