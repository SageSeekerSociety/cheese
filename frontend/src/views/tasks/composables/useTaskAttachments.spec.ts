import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  listTaskFiles: vi.fn(),
  downloadFile: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/api/taskFiles', () => ({
  listTaskFiles: mocks.listTaskFiles,
  taskFileRawUrl: (taskId: number, name: string) => `/api/tasks/${taskId}/files/raw?name=${encodeURIComponent(name)}`,
}))
vi.mock('@/api', () => ({ downloadFile: mocks.downloadFile }))

import { useTaskAttachments } from './useTaskAttachments'

const file = () => ({ name: '讲义.pdf', size: 2048, addedAt: 0 })

describe('一道题的材料', () => {
  beforeEach(() => {
    mocks.listTaskFiles.mockReset()
    mocks.downloadFile.mockClear()
  })

  it('清单和能不能下载都照服务端给的', async () => {
    mocks.listTaskFiles.mockResolvedValue({ files: [file()], canDownload: false })
    const a = useTaskAttachments()

    await a.load(12)
    expect(a.attachments.value.map((f) => f.name)).toEqual(['讲义.pdf'])
    expect(a.canDownload.value).toBe(false)
  })

  it('清单取不到时什么都不给，也不给下载', async () => {
    mocks.listTaskFiles.mockRejectedValue(new Error('HTTP 500'))
    const a = useTaskAttachments()

    await a.load(12)
    expect(a.attachments.value).toEqual([])
    expect(a.canDownload.value).toBe(false)
  })

  it('已有清单但再次读取被拒绝时会清空旧清单', async () => {
    mocks.listTaskFiles.mockResolvedValueOnce({ files: [file()], canDownload: true })
    const a = useTaskAttachments()
    await a.load(12)

    mocks.listTaskFiles.mockRejectedValueOnce(new Error('HTTP 403'))
    await a.load(12)

    expect(a.attachments.value).toEqual([])
    expect(a.canDownload.value).toBe(false)
  })

  it('还没有题目 id 就不问', async () => {
    const a = useTaskAttachments()

    await a.load(null)
    expect(mocks.listTaskFiles).not.toHaveBeenCalled()
  })

  it('下载的是名字点到的那一份，存成它自己的名字', async () => {
    mocks.listTaskFiles.mockResolvedValue({ files: [file()], canDownload: true })
    const a = useTaskAttachments()
    await a.load(12)

    await a.download(12, a.attachments.value[0])
    const [url, name] = mocks.downloadFile.mock.calls[0]
    expect(new URL(url, 'http://x').searchParams.get('name')).toBe('讲义.pdf')
    expect(name).toBe('讲义.pdf')
    expect(a.downloading.value).toBeNull()
  })
})
