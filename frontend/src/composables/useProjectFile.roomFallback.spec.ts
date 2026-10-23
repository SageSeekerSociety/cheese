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
  workspaceFileRawUrl: () => '',
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

function open() {
  return useProjectFile({ projectId: () => 'p1', channelId: () => 'c1', path: () => 'gemm_bench.cu' })
}

describe('useProjectFile reads the channel’s own file when the project has none', () => {
  it('opens a room file in a project without a repository', async () => {
    mocks.readFile.mockRejectedValue(new mocks.ApiError('项目没有代码仓库', 503))
    mocks.readPreviewFile.mockResolvedValue(TEXT)
    const file = open()
    await vi.waitFor(() => expect(file.loading.value).toBe(false))
    expect(mocks.readPreviewFile).toHaveBeenCalledWith('c1', 'gemm_bench.cu')
    expect(file.error.value).toBeNull()
    expect(file.missing.value).toBe(false)
    expect(file.inRoom.value).toBe(true)
    expect(file.content.value).toBe(TEXT.content)
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
    expect(mocks.readPreviewFile).not.toHaveBeenCalled()
    expect(file.inRoom.value).toBe(false)
    expect(file.content.value).toBe('from repo')
  })
})
