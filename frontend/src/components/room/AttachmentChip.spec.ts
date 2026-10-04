/** 待发条上一枚附件的合同。
 *
 * 钉的是「传上去的那一枚和没传上去的那一枚长得不一样，且各有一条能走的路」：正常
 * 的那枚只有 ×；失败的那枚多一颗重试，因为它带着 File 还留在条上，重试真的能再传。
 * 这一份不渲染整条待发条（那在 RoomComposer.spec 里），只问这一枚自己。
 */
import type { PendingAttachment } from '../../lib/attachments'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AttachmentChip from './AttachmentChip.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

function mount(attachment: PendingAttachment) {
  const onRemove = vi.fn()
  const onRetry = vi.fn()
  const Wrapper = defineComponent({
    setup: () => () => h(AttachmentChip, { topicId: 't1', attachment, onRemove, onRetry }),
  })
  const utils = render(Wrapper, { global: { plugins: [createVuetify({ components, directives })] } })
  return { ...utils, onRemove, onRetry }
}

describe('a waiting attachment', () => {
  it('offers a way to take an uploaded file back, and nothing to retry', () => {
    const { getByRole, queryByRole } = mount({ path: 'uploads/id/notes.txt', mime: 'text/plain', name: 'notes.txt' })
    expect(getByRole('button', { name: '移除 notes.txt' })).toBeTruthy()
    expect(queryByRole('button', { name: /重试/ })).toBeNull()
  })

  it('a failed upload keeps its place and offers retry next to remove', async () => {
    const { getByRole, onRetry, onRemove } = mount({
      path: 'uploading:1:notes.txt',
      mime: 'text/plain',
      name: 'notes.txt',
      error: true,
      file: new File(['x'], 'notes.txt', { type: 'text/plain' }),
    })

    // 两条路都点名说的是哪一枚：读屏读到的不是「重试」「移除」两个光杆。
    const retry = getByRole('button', { name: '重试上传 notes.txt' })
    expect(getByRole('button', { name: '移除 notes.txt' })).toBeTruthy()

    await fireEvent.click(retry)
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onRemove).not.toHaveBeenCalled()

    await fireEvent.click(getByRole('button', { name: '移除 notes.txt' }))
    expect(onRemove).toHaveBeenCalledTimes(1)
  })
})

describe('an uploading attachment', () => {
  it('spins while the server has not told us a total', () => {
    const { container } = mount({
      path: 'uploading:1:big.bin',
      mime: 'application/octet-stream',
      name: 'big.bin',
      uploading: true,
    })
    const ring = container.querySelector('.v-progress-circular')!
    // 还不知道总量，只能转圈——报一个数就是在编。
    expect(ring.classList.contains('v-progress-circular--indeterminate')).toBe(true)
  })

  it('draws a determinate ring once progress comes in', () => {
    const { container } = mount({
      path: 'uploading:1:big.bin',
      mime: 'application/octet-stream',
      name: 'big.bin',
      uploading: true,
      progress: 0.42,
    })
    const ring = container.querySelector('.v-progress-circular')!
    // 有总量就是确定的圈：一个 9MB 的文件现在能说出自己走了多少。
    expect(ring.classList.contains('v-progress-circular--indeterminate')).toBe(false)
    expect(ring.getAttribute('aria-valuenow')).toBe('42')
  })
})
