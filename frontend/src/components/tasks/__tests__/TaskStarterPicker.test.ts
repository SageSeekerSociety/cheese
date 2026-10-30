import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ toastError: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: mocks.toastError } }))

import { type StarterTarget, useStarterUpload } from '@/composables/useStarterUpload'

import TaskStarterPicker from '../TaskStarterPicker.vue'

import i18n, { setLocale } from '@/i18n'

// 确认框是一个 VOverlay，而 happy-dom 没有 visualViewport：不补这几样，弹窗挂不上去。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

/** 确认框里的那颗按钮（行上那颗「移除」也写着同一个字）。 */
const dialogButton = (label: string) =>
  within(screen.getByRole('alertdialog'))
    .getAllByRole('button')
    .find((b) => b.textContent?.trim() === label) as HTMLElement

const zip = (name = 'lab1.zip', bytes = 4096) => new File([new Uint8Array(bytes)], name, { type: 'application/zip' })

/** 页面那样用：上传与「现在是哪一份」在 composable 里，这一格只画。 */
function mount(target: StarterTarget<number>, confirmRemove = false) {
  let current: () => number | null = () => null
  const Page = defineComponent({
    setup() {
      const starter = useStarterUpload<number>(target)
      current = () => starter.file.value?.id ?? null
      return () =>
        h(components.VApp, null, () =>
          h(TaskStarterPicker, {
            file: starter.file.value,
            uploading: starter.uploading.value,
            percent: starter.percent.value,
            confirmRemove,
            onPick: starter.pick,
            onRemove: starter.remove,
          })
        )
    },
  })
  const view = render(Page, { global: { plugins: [createVuetify({ components }), i18n] } })
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

  it('「替换」「移除」两颗按钮写着字，不用把鼠标停上去猜', async () => {
    const upload = vi.fn(async (file: File) => ({ id: 1, name: file.name, size: file.size }))
    const { view, input } = mount({ upload })
    await fireEvent.change(input, { target: { files: [zip()] } })

    await waitFor(() => expect(view.getByRole('button', { name: '替换起始代码' }).textContent?.trim()).toBe('替换'))
    expect(view.getByRole('button', { name: '移除起始代码' }).textContent?.trim()).toBe('移除')
  })

  it('改题页移除起始代码先问一句：取消就还在，答「移除」才移除', async () => {
    const remove = vi.fn().mockResolvedValue(undefined)
    const upload = vi.fn(async (file: File) => ({ id: 7, name: file.name, size: file.size }))
    const { view, input, current } = mount({ upload, remove }, true)
    await fireEvent.change(input, { target: { files: [zip()] } })
    await waitFor(() => expect(current()).toBe(7))

    await fireEvent.click(view.getByRole('button', { name: '移除起始代码' }))
    await waitFor(() => expect(screen.getByRole('alertdialog', { name: '移除起始代码？' })).toBeTruthy())
    await fireEvent.click(dialogButton('取消'))
    expect(remove).not.toHaveBeenCalled()
    expect(current()).toBe(7)

    await fireEvent.click(view.getByRole('button', { name: '移除起始代码' }))
    await waitFor(() => expect(dialogButton('移除')).toBeTruthy())
    await fireEvent.click(dialogButton('移除'))
    await waitFor(() => expect(current()).toBeNull())
  })

  it('页面给了上限：说明里写着多大收得下，更大的去改题页传', () => {
    const view = render(TaskStarterPicker, {
      props: { file: null, uploading: false, maxBytes: 100 * 1024 * 1024 },
      global: { plugins: [createVuetify({ components }), i18n] },
    })
    expect(view.container.textContent).toContain('不超过 100 MB')
    expect(view.container.textContent).toContain('改题页')
  })

  it('字节传完了平台还在收：写「处理中」，不停在「上传中 100%」', async () => {
    const upload = vi.fn(
      (file: File, progress: (sent: number, total: number) => void) =>
        new Promise<{ id: number; name: string; size: number }>(() => {
          progress(file.size, file.size)
        })
    )
    const { view, input } = mount({ upload })
    await fireEvent.change(input, { target: { files: [zip()] } })

    await waitFor(() => expect(view.getByRole('button', { name: /处理中/ })).toBeTruthy())
    expect(view.queryByRole('button', { name: /100%/ })).toBeNull()
  })
})
