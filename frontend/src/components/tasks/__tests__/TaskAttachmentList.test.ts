import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  listAttachments: vi.fn(),
  downloadFile: vi.fn().mockResolvedValue(undefined),
  taskAttachmentRawUrl: vi.fn(
    (taskId: number, attachmentId: number) => `/api/tasks/${taskId}/attachments/${attachmentId}/download`
  ),
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: { listAttachments: mocks.listAttachments } }))
vi.mock('@/api', () => ({
  downloadFile: mocks.downloadFile,
  taskAttachmentRawUrl: mocks.taskAttachmentRawUrl,
}))

import TaskAttachmentList from '../TaskAttachmentList.vue'

const file = (overrides: Record<string, unknown> = {}) => ({
  id: 3,
  name: '讲义.pdf',
  size: 2048,
  contentType: 'application/pdf',
  uploaderId: 1,
  downloadCount: 0,
  createdAt: 0,
  ...overrides,
})

const mount = (taskId: number | null = 12) =>
  render(TaskAttachmentList, {
    props: { taskId },
    global: { plugins: [createVuetify()] },
  })

describe('题目附件那一块', () => {
  beforeEach(() => {
    mocks.listAttachments.mockReset()
    mocks.downloadFile.mockClear()
    mocks.taskAttachmentRawUrl.mockClear()
  })

  it('看得见这道题就看得见清单，拿得到的人那一行是「下载」', async () => {
    mocks.listAttachments.mockResolvedValue({
      data: { attachments: [file()], canDownload: true },
    })
    const view = mount()

    await waitFor(() => expect(view.getByTestId('task-attachment')).toBeTruthy())
    expect(mocks.listAttachments).toHaveBeenCalledWith(12)
    expect(view.container.textContent).toContain('讲义.pdf')
    expect(view.container.textContent).toContain('2.00 KB')
    expect(view.getByRole('button', { name: /下载/ })).toBeTruthy()
    view.unmount()
  })

  it('没领这道题的人看得到清单，那一行写的是「领取这道题之后才能下载」', async () => {
    mocks.listAttachments.mockResolvedValue({
      data: { attachments: [file()], canDownload: false },
    })
    const view = mount()

    await waitFor(() => expect(view.getByTestId('task-attachment')).toBeTruthy())
    expect(view.container.textContent).toContain('讲义.pdf')
    expect(view.container.textContent).toContain('领取这道题之后才能下载')
    expect(view.queryByRole('button', { name: /下载/ })).toBeNull()
    view.unmount()
  })

  it('点下载走的是那道带鉴权的门，计数跟着涨一格', async () => {
    mocks.listAttachments.mockResolvedValue({
      data: { attachments: [file()], canDownload: true },
    })
    const view = mount()

    await waitFor(() => expect(view.getByTestId('task-attachment')).toBeTruthy())
    expect(view.container.textContent).toContain('已下载 0 次')

    await fireEvent.click(view.getByRole('button', { name: /下载/ }))

    await waitFor(() => expect(mocks.downloadFile).toHaveBeenCalledTimes(1))
    expect(mocks.taskAttachmentRawUrl).toHaveBeenCalledWith(12, 3)
    expect(mocks.downloadFile.mock.calls[0][1]).toBe('讲义.pdf')
    await waitFor(() => expect(view.container.textContent).toContain('已下载 1 次'))
    view.unmount()
  })

  it('这道题没有材料就整块不出现', async () => {
    mocks.listAttachments.mockResolvedValue({
      data: { attachments: [], canDownload: true },
    })
    const view = mount()

    await waitFor(() => expect(mocks.listAttachments).toHaveBeenCalled())
    expect(view.container.textContent).not.toContain('题目附件')
    view.unmount()
  })

  it('清单取不到时也不显示成「这道题没有材料」', async () => {
    mocks.listAttachments.mockRejectedValue(new Error('HTTP 500'))
    const view = mount()

    await waitFor(() => expect(mocks.listAttachments).toHaveBeenCalled())
    expect(view.container.textContent).not.toContain('题目附件')
    view.unmount()
  })

  it('还没有题目 id 就不问', async () => {
    const view = mount(null)

    await Promise.resolve()
    expect(mocks.listAttachments).not.toHaveBeenCalled()
    view.unmount()
  })
})
