import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ toastError: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: mocks.toastError } }))

import { type StarterTarget, useStarterUpload } from '@/composables/useStarterUpload'

import TaskStarterPicker from '../TaskStarterPicker.vue'

import i18n, { setLocale } from '@/i18n'

const zip = (name = 'lab1.zip', bytes = 4096) => new File([new Uint8Array(bytes)], name, { type: 'application/zip' })

/** 页面那样用：上传与「现在是哪一份」在 composable 里，这一格只画。 */
function mount(target: StarterTarget<number>) {
  let current: () => number | null = () => null
  const Page = defineComponent({
    setup() {
      const starter = useStarterUpload<number>(target)
      current = () => starter.file.value?.id ?? null
      return () =>
        h(TaskStarterPicker, {
          file: starter.file.value,
          uploading: starter.uploading.value,
          percent: starter.percent.value,
          onPick: starter.pick,
          onRemove: starter.remove,
        })
    },
  })
  const view = render(Page, { global: { plugins: [createVuetify(), i18n] } })
  const input = view.getByTestId('starter-input') as HTMLInputElement
  return { view, input, current: () => current() }
}

describe('题目的起始代码', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    mocks.toastError.mockReset()
  })

  it('选一份 .zip 就传上去，列出它；换一份后带的是新的那份', async () => {
    let next = 1
    const upload = vi.fn(async (file: File) => ({ id: next++, name: file.name, size: file.size }))
    const { view, input, current } = mount({ upload })

    await fireEvent.change(input, { target: { files: [zip()] } })
    await waitFor(() => expect(view.getByTestId('starter-file').textContent).toContain('lab1.zip'))
    expect(current()).toBe(1)

    await fireEvent.change(input, { target: { files: [zip('lab1-v2.zip')] } })
    await waitFor(() => expect(view.getByTestId('starter-file').textContent).toContain('lab1-v2.zip'))
    expect(current()).toBe(2)
  })

  it('服务端说这份不行：弹出原因，不当作起始代码', async () => {
    const upload = vi.fn().mockRejectedValue(new Error('压缩包里没有文件'))
    const { view, input, current } = mount({ upload })

    await fireEvent.change(input, { target: { files: [zip()] } })

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('起始代码上传失败：压缩包里没有文件'))
    expect(view.queryByTestId('starter-file')).toBeNull()
    expect(current()).toBeNull()
  })

  it('移除后不再带起始代码；服务端没删成就还留着', async () => {
    const remove = vi.fn().mockRejectedValueOnce(new Error('稍后重试')).mockResolvedValue(undefined)
    const upload = vi.fn(async (file: File) => ({ id: 7, name: file.name, size: file.size }))
    const { view, input, current } = mount({ upload, remove })
    await fireEvent.change(input, { target: { files: [zip()] } })
    await waitFor(() => expect(current()).toBe(7))

    await fireEvent.click(view.getByRole('button', { name: '移除起始代码' }))
    await waitFor(() => expect(mocks.toastError).toHaveBeenCalled())
    expect(current()).toBe(7)

    await fireEvent.click(view.getByRole('button', { name: '移除起始代码' }))
    await waitFor(() => expect(current()).toBeNull())
  })

  it('传起始代码时写着传到了百分之几', async () => {
    const upload = vi.fn(
      (file: File, progress: (sent: number, total: number) => void) =>
        new Promise<{ id: number; name: string; size: number }>(() => {
          progress(file.size / 2, file.size)
        })
    )
    const { view, input } = mount({ upload })
    await fireEvent.change(input, { target: { files: [zip()] } })

    await waitFor(() => expect(view.getByRole('button', { name: /上传中 50%/ })).toBeTruthy())
  })

  it('只有图标的「替换」「移除」两颗按钮，鼠标停上去写着各自做什么', async () => {
    const upload = vi.fn(async (file: File) => ({ id: 1, name: file.name, size: file.size }))
    const { view, input } = mount({ upload })
    await fireEvent.change(input, { target: { files: [zip()] } })

    await waitFor(() => expect(view.getByTitle('替换起始代码')).toBeTruthy())
    expect(view.getByTitle('移除起始代码')).toBeTruthy()
  })
})
