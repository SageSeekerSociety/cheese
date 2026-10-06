/** 慢网下的预览面板：字节在路上时要**一直**有骨架（而不是一行字闪一下就没），
 *  读失败时要**留在原地**给出原因和一条重试，而不是留一扇空窗。这里锁三件事：
 *  在途是骨架、失败是可重试的错误块、点了重试真的再读一次。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import FileBytesPreview from './FileBytesPreview.vue'

import { setLocale } from '@/i18n'

// 预览正文的几种真身各拖一整套解析库（pdfjs / pptx），这一组用例只关心它外围的
// 「在途 / 失败 / 重试」三态，替身足够。
vi.mock('@/components/panels/preview/PreviewPages.vue', () => ({
  default: { template: '<div data-testid="pages" />' },
}))
vi.mock('@/components/panels/preview/PreviewSheet.vue', () => ({
  default: { template: '<div data-testid="sheet" />' },
}))
vi.mock('@/components/panels/preview/PreviewSlides.vue', () => ({
  default: { template: '<div data-testid="slides" />' },
}))
vi.mock('@/components/panels/preview/DesignImage.vue', () => ({ default: { template: '<img />' } }))

beforeEach(() => setLocale('en'))
afterEach(cleanup)

function mount(read: (asPdf: boolean) => Promise<ArrayBuffer>) {
  return render(FileBytesPreview, {
    props: { filename: 'notes.txt', source: 'notes.txt#1', read },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('FileBytesPreview', () => {
  it('keeps a skeleton on screen while the bytes are in flight', async () => {
    const view = mount(() => new Promise<ArrayBuffer>(() => {}))

    // 在途 = 骨架一直在（不是一行字），且没有错误块。
    expect(await screen.findByRole('status')).toBeTruthy()
    expect(view.container.querySelector('.skel')).toBeTruthy()
    expect(view.container.querySelector('.base-load-error')).toBeNull()
  })

  it('replaces a failed read with the reason and a retry that reads again', async () => {
    const bytes = new TextEncoder().encode('hello from the file').buffer as ArrayBuffer
    const read = vi.fn<(asPdf: boolean) => Promise<ArrayBuffer>>()
    read.mockRejectedValueOnce(new Error('HTTP 502 for /bytes')).mockResolvedValueOnce(bytes)
    const view = mount(read)

    // 失败：留在原地、说清原因、给出重试；骨架已经让位。
    expect(await screen.findByText('Could not preview this file')).toBeTruthy()
    expect(screen.getByText('HTTP 502 for /bytes')).toBeTruthy()
    expect(view.container.querySelector('.skel')).toBeNull()

    // 重试：真的再读一次，成功后把内容摆出来。
    await fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('hello from the file')).toBeTruthy()
    expect(read).toHaveBeenCalledTimes(2)
  })
})
