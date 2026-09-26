import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ upload: vi.fn() }))
vi.mock('@/network/api/attachments', () => ({ AttachmentsApi: { upload: mocks.upload } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import TaskAttachmentPicker from '../TaskAttachmentPicker.vue'

const pdf = (name = '讲义.pdf', bytes = 2048) => new File([new Uint8Array(bytes)], name, { type: 'application/pdf' })

const mount = () => render(TaskAttachmentPicker, { global: { plugins: [createVuetify()] } })

const fileInputOf = (view: ReturnType<typeof render>) =>
  view.container.querySelector('input[type="file"]') as HTMLInputElement

describe('发题时带的材料', () => {
  // 这几条按「传了几次」下断言，调用记录必须一条一条分开数。
  beforeEach(() => mocks.upload.mockReset())

  it('选中即上传，并把拿到的 id 交给发题那条请求', async () => {
    mocks.upload.mockResolvedValue({ data: { id: 7 } })
    const view = mount()

    await fireEvent.change(fileInputOf(view), { target: { files: [pdf()] } })

    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(1))
    expect(mocks.upload.mock.calls[0][0].type).toBe('file')
    expect(mocks.upload.mock.calls[0][0].file.name).toBe('讲义.pdf')
    await waitFor(() => expect(view.emitted()['update:attachmentIds']?.at(-1)).toEqual([[7]]))
    // 传完就看得见：名字与大小都在那张列表上。
    expect(view.getByTestId('attached-file').textContent).toContain('讲义.pdf')
    expect(view.getByTestId('attached-file').textContent).toContain('2.00 KB')
    view.unmount()
  })

  it('一次选多个就依次上传，最后交出去的是全部的 id', async () => {
    mocks.upload.mockResolvedValueOnce({ data: { id: 7 } }).mockResolvedValueOnce({ data: { id: 8 } })
    const view = mount()

    await fireEvent.change(fileInputOf(view), {
      target: { files: [pdf('讲义.pdf'), pdf('数据.csv', 512)] },
    })

    await waitFor(() => expect(mocks.upload).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(view.emitted()['update:attachmentIds']?.at(-1)).toEqual([[7, 8]]))
    expect(view.getAllByTestId('attached-file')).toHaveLength(2)
    view.unmount()
  })

  it('传不上去的那一份不会混进 id 里，剩下的照样交出去', async () => {
    mocks.upload.mockRejectedValueOnce(new Error('HTTP 413')).mockResolvedValueOnce({ data: { id: 8 } })
    const view = mount()

    await fireEvent.change(fileInputOf(view), {
      target: { files: [pdf('太大.pdf'), pdf('小的.pdf', 128)] },
    })

    await waitFor(() => expect(view.emitted()['update:attachmentIds']?.at(-1)).toEqual([[8]]))
    expect(view.container.textContent).not.toContain('太大.pdf')
    expect(view.container.textContent).toContain('小的.pdf')
    view.unmount()
  })

  it('上传期间举旗，结束后放下 —— 发题页据此挡住提交', async () => {
    mocks.upload.mockResolvedValue({ data: { id: 7 } })
    const view = mount()

    await fireEvent.change(fileInputOf(view), { target: { files: [pdf()] } })

    // 两次都要看全，不能只看最后一下：顺序写死，「先放旗后举旗」这种错才会红。
    await waitFor(() => expect(view.emitted()['update:uploading']?.at(-1)).toEqual([false]))
    expect(view.emitted()['update:uploading']).toEqual([[true], [false]])
    view.unmount()
  })

  it('移除一份材料之后，交出去的 id 里不再有它', async () => {
    mocks.upload.mockResolvedValue({ data: { id: 7 } })
    const view = mount()

    await fireEvent.change(fileInputOf(view), { target: { files: [pdf()] } })
    await waitFor(() => expect(view.getAllByTestId('attached-file')).toHaveLength(1))

    await fireEvent.click(view.getByRole('button', { name: '移除 讲义.pdf' }))

    await waitFor(() => expect(view.emitted()['update:attachmentIds']?.at(-1)).toEqual([[]]))
    expect(view.queryByTestId('attached-file')).toBeNull()
    view.unmount()
  })
})
