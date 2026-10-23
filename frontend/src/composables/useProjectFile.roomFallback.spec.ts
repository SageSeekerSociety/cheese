/** 频道里一份 `.cu` 交付物（预览画不出，所以 chip 把它交给「项目当前版本」那一格），在
 *  没有代码仓库的项目里点开，原来只剩一句「项目没有代码仓库」。读不到项目版本时先看它是
 *  不是频道自己的文件，是就照样打开。 */

import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  class ApiError extends Error {
    constructor(
      message: string,
      public status: number
    ) {
      super(message)
    }
  }
  return {
    ApiError,
    readFile: vi.fn(),
    readPreviewFile: vi.fn(),
    listRoomTasks: vi.fn(),
  }
})
vi.mock('../api', () => ({
  ApiError: mocks.ApiError,
  downloadFile: vi.fn(),
  getGitDiff: vi.fn(),
  listRoomTasks: mocks.listRoomTasks,
  readFile: mocks.readFile,
  readPreviewFile: mocks.readPreviewFile,
  attachmentRawUrl: (topic: string, path: string) => `/api/topics/${topic}/attachments/raw?path=${path}`,
  workspaceFileRawUrl: () => '/api/projects/p1/file/raw',
}))

import { useProjectFile } from './useProjectFile'

const TEXT = {
  path: 'gemm_bench.cu',
  content: '__global__ void gemm() {}\n',
  version: 'v1',
  bytes: 26,
  binary: false,
  too_large: false,
}

function open(path = 'gemm_bench.cu') {
  return useProjectFile({ projectId: () => 'p1', channelId: () => 'c1', path: () => path })
}

describe('useProjectFile reads the channel’s own file when the project has none', () => {
  it('opens a room file in a project without a repository', async () => {
    mocks.readFile.mockRejectedValue(new mocks.ApiError('项目没有代码仓库', 503))
    mocks.readPreviewFile.mockResolvedValue(TEXT)
    const file = open()
    await vi.waitFor(() => expect(file.loading.value).toBe(false))
    expect(file.error.value).toBeNull()
    expect(file.missing.value).toBe(false)
    expect(file.inRoom.value).toBe(true)
    expect(file.content.value).toBe(TEXT.content)
    // 「下载原文件」和图片也从频道取，不再指着项目里不存在的那一份。
    expect(file.rawUrl.value).toContain('/topics/c1/attachments/raw?path=gemm_bench.cu')
  })

  it('opens the room file when the repository simply does not have that path', async () => {
    mocks.readFile.mockRejectedValue(new mocks.ApiError('not found', 404))
    mocks.readPreviewFile.mockResolvedValue(TEXT)
    const file = open()
    await vi.waitFor(() => expect(file.loading.value).toBe(false))
    expect(file.missing.value).toBe(false)
    expect(file.inRoom.value).toBe(true)
  })

  it('does not draw a channel .bmp as an image: the channel only serves it for download', async () => {
    mocks.readFile.mockRejectedValue(new mocks.ApiError('not found', 404))
    mocks.readPreviewFile.mockResolvedValue({ ...TEXT, path: 'shot.bmp', content: null, binary: true })
    const file = open('shot.bmp')
    await vi.waitFor(() => expect(file.loading.value).toBe(false))
    expect(file.inRoom.value).toBe(true)
    expect(file.isImage.value).toBe(false)
    expect(file.binary.value).toBe(true)
  })

  it('still reports the project error when the channel does not have it either', async () => {
    mocks.readFile.mockRejectedValue(new mocks.ApiError('项目没有代码仓库', 503))
    mocks.readPreviewFile.mockRejectedValue(new mocks.ApiError('not found', 404))
    const file = open()
    await vi.waitFor(() => expect(file.loading.value).toBe(false))
    expect(file.error.value).toBe('项目没有代码仓库')
    expect(file.inRoom.value).toBe(false)
  })

  it('reads the project version first when it has the file', async () => {
    mocks.readPreviewFile.mockReset()
    mocks.readFile.mockResolvedValue({ ...TEXT, content: 'from repo' })
    const file = open()
    await vi.waitFor(() => expect(file.loading.value).toBe(false))
    expect(file.inRoom.value).toBe(false)
    expect(file.content.value).toBe('from repo')
  })
})
