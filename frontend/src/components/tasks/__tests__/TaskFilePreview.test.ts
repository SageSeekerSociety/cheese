import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TaskFilePreview from '../TaskFilePreview.vue'

import i18n, { setLocale } from '@/i18n'
// 资料库那一页写文件大小用的那一个（`LibraryFileDetail.vue`）。
import { fmtBytes } from '@/lib/changesTree'

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
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const mount = (file: { name: string; size: number } | null, read = vi.fn(async () => new ArrayBuffer(0))) => {
  const view = render(TaskFilePreview, {
    props: { file, read },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  return { ...view, read }
}

describe('题目页上点开的一份材料', () => {
  it('打开就按它的名字读出来摆着；Word 文档要服务端转好的 PDF', async () => {
    const { read } = mount(
      { name: '实验指导.txt', size: 12 },
      vi.fn(async () => new TextEncoder().encode('第一步').buffer)
    )
    await waitFor(() => expect(screen.getByText('第一步')).toBeTruthy())
    expect(read).toHaveBeenCalledWith(false)

    cleanup()
    const word = mount({ name: '实验报告模板.docx', size: 12 })
    await waitFor(() => expect(word.read).toHaveBeenCalledWith(true))
  })

  it('超过资料库那条预览上限的一份不读，说只能下载，下载还在', async () => {
    const { read, emitted } = mount({ name: '数据集.csv', size: 300 * 1024 * 1024 })
    await waitFor(() => expect(screen.getByText(/200 MB/)).toBeTruthy())
    expect(read).not.toHaveBeenCalled()
    await fireEvent.click(screen.getByRole('button', { name: '下载' }))
    expect(emitted('download')).toEqual([[]])
  })

  it('文件大小和资料库里写得一样', async () => {
    mount({ name: '数据集.csv', size: 350 * 1024 * 1024 })
    await waitFor(() => expect(screen.getByText(fmtBytes(350 * 1024 * 1024))).toBeTruthy())
  })

  it('关上就报关闭', async () => {
    const { emitted } = mount({ name: '实验指导.txt', size: 12 })
    await fireEvent.click(await screen.findByRole('button', { name: '关闭' }))
    expect(emitted('close')).toEqual([[]])
  })
})
