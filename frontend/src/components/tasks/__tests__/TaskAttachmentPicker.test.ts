import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 附件那一格上的上限问的是 `GET /attachments/limits`：与上传同一道门。
const mocks = vi.hoisted(() => ({ limits: vi.fn(), toastError: vi.fn() }))
vi.mock('@/network/api/attachments', () => ({ AttachmentsApi: { limits: mocks.limits } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: mocks.toastError } }))

import { useAttachmentUploads } from '@/composables/useAttachmentUploads'

import TaskAttachmentPicker from '../TaskAttachmentPicker.vue'

import i18n, { setLocale } from '@/i18n'

const pdf = (name = '讲义.pdf', bytes = 2048) => new File([new Uint8Array(bytes)], name, { type: 'application/pdf' })

/** 接口报的单份文件上限。不是任何一版页面里写过的数：那句话若对得上它，就只可能是
 *  照着接口报的写的。 */
const LIMIT_BYTES = 12_345_678

/** 页面那样用：上传与名单在 composable 里，附件那一格只画。`ids` 就是发题那条请求带的。 */
function mount(target: Parameters<typeof useAttachmentUploads>[0]) {
  let ids: () => number[] = () => []
  let uploading: () => boolean = () => false
  const Page = defineComponent({
    setup() {
      const uploads = useAttachmentUploads(target)
      ids = () => uploads.ids.value
      uploading = () => uploads.uploading.value
      return () =>
        h(TaskAttachmentPicker, {
          files: uploads.files.value,
          uploading: uploads.uploading.value,
          maxFileBytes: uploads.maxFileBytes.value,
          onAdd: uploads.add,
          onRemove: uploads.remove,
        })
    },
  })
  const view = render(Page, { global: { plugins: [createVuetify(), i18n] } })
  const input = view.getByTestId('attachment-input') as HTMLInputElement
  return { view, input, ids: () => ids(), uploading: () => uploading() }
}

describe('题目带的材料', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    mocks.limits.mockReset()
    mocks.toastError.mockReset()
    mocks.limits.mockResolvedValue({ data: { maxFileBytes: LIMIT_BYTES } })
  })

  it('写的上限是接口报的那个数；问不到就不写这句话，选择照旧', async () => {
    const { view } = mount({ upload: vi.fn() })
    await waitFor(() => expect(view.container.textContent).toContain('单个文件不超过 11.77 MB'))
    view.unmount()

    mocks.limits.mockRejectedValue(new Error('503 Service Unavailable'))
    const offline = mount({ upload: vi.fn() })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(offline.view.container.textContent).not.toContain('单个文件不超过')
    expect(offline.input).toBeTruthy()
    offline.view.unmount()
  })

  it('选中即上传，一次选几个就依次传几个，发题带的是全部的 id', async () => {
    const upload = vi.fn().mockResolvedValueOnce(7).mockResolvedValueOnce(8)
    const { view, input, ids } = mount({ upload })

    await fireEvent.change(input, { target: { files: [pdf('讲义.pdf'), pdf('数据.csv', 512)] } })

    await waitFor(() => expect(ids()).toEqual([7, 8]))
    expect(upload.mock.calls.map(([file]) => (file as File).name)).toEqual(['讲义.pdf', '数据.csv'])
    expect(view.getAllByTestId('attached-file')).toHaveLength(2)
    view.unmount()
  })

  it('传不上去的那一份不混进 id 里，说清是哪一份；剩下的照样带上', async () => {
    const upload = vi.fn().mockRejectedValueOnce(new Error('HTTP 413')).mockResolvedValueOnce(8)
    const { view, input, ids } = mount({ upload })

    await fireEvent.change(input, { target: { files: [pdf('太大.pdf'), pdf('小的.pdf', 128)] } })

    await waitFor(() => expect(ids()).toEqual([8]))
    expect(mocks.toastError.mock.calls[0][0]).toContain('太大.pdf')
    expect(view.container.textContent).not.toContain('太大.pdf')
    view.unmount()
  })

  it('上传期间举着，传完放下：发题页据此挡住提交', async () => {
    let finish: (id: number) => void = () => {}
    const upload = vi.fn(() => new Promise<number>((resolve) => (finish = resolve)))
    const { view, input, uploading } = mount({ upload })

    await fireEvent.change(input, { target: { files: [pdf()] } })
    await waitFor(() => expect(uploading()).toBe(true))

    finish(7)
    await waitFor(() => expect(uploading()).toBe(false))
    view.unmount()
  })

  it('去掉一份：改题时从这道题上摘下来，摘不下来就还在', async () => {
    const remove = vi.fn().mockRejectedValueOnce(new Error('HTTP 500')).mockResolvedValueOnce(undefined)
    const { view, input, ids } = mount({ upload: vi.fn().mockResolvedValue(7), remove })
    await fireEvent.change(input, { target: { files: [pdf()] } })
    await waitFor(() => expect(ids()).toEqual([7]))

    await fireEvent.click(view.getByRole('button', { name: '移除 讲义.pdf' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith(7))
    expect(ids()).toEqual([7])

    await fireEvent.click(view.getByRole('button', { name: '移除 讲义.pdf' }))
    await waitFor(() => expect(ids()).toEqual([]))
    expect(view.queryByTestId('attached-file')).toBeNull()
    view.unmount()
  })
})
