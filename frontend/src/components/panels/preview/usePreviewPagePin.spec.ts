/**
 * 这条出口要等截图拍下来、传上去，是预览里唯一一条 await 之后才撤掉定位条的路。
 * 等的那一段里输入框和发送按钮都还在，人能再点一次 —— 而 `submit` 每次都给一个新的
 * request_id，后端不按它去重，于是同一个位置发出两条消息、多传一张图。
 *
 * 直接盯这个 composable，而不是从面板上点两下：那条路要先过 DOM 和事件层，第二次点
 * 下去到底有没有走到 `send` 不容易看清，从面板上写出来的用例也盖不住这条。
 */
import type { UploadAnnotation } from '../../../composables/usePanelPreview'
import type { SubmitPreviewQuestion } from '../../../lib/previewQuestion'

import { beforeEach, expect, it, vi } from 'vitest'

import { usePreviewPagePin } from './usePreviewPagePin'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const context = {
  topicId: 'room',
  path: 'deck.pdf',
  source: 'committed' as const,
  taskId: 'task',
  version: 'v7',
}
const docBytes = new ArrayBuffer(8)

function setup(upload: UploadAnnotation) {
  const submit = vi.fn().mockReturnValue(true)
  const deps = { snapshot: async () => ({ blob: new Blob(['png']) }), open: vi.fn(), clear: vi.fn() }
  const pin = usePreviewPagePin(
    {
      topicId: 'room',
      submitQuestion: submit as unknown as SubmitPreviewQuestion,
      uploadAnnotation: upload,
      previewFile: {
        path: 'deck.pdf',
        content: null,
        version: 'v7',
        bytes: 8,
        binary: true,
        too_large: false,
        source: context.source,
      },
      docIdentity: context,
      docSnapshot: { bytes: docBytes, identity: context, sourceVersion: context.version },
      docBytes,
      slideContext: context,
      docLoading: false,
      docError: '',
      docRendererMissing: false,
    },
    deps
  )
  pin.pin.value = { page: 3, x: 0.42, y: 0.17, context }
  return { pin, submit, deps }
}

it('图还在传的时候再发一次，只发一条', async () => {
  // 图传得慢：两次 send 都停在这一步。两次都放行之后才算数。
  const gates: (() => void)[] = []
  const upload = vi.fn(
    () =>
      new Promise<{ id: string; name: string }>((resolve) => {
        gates.push(() => resolve({ id: 'a1', name: 'page-3.png' }))
      })
  )
  const { pin, submit } = setup(upload as unknown as UploadAnnotation)

  const first = pin.send('这里不对')
  const second = pin.send('这里不对')
  // 先让能走到上传的那几条都停在 gate 上，再一起放行。
  await new Promise((r) => setTimeout(r, 0))
  for (const release of gates) release()
  await Promise.all([first, second])

  expect(upload).toHaveBeenCalledTimes(1)
  expect(submit).toHaveBeenCalledTimes(1)
})

it('发完一次之后还能再发第二次（守卫不会把出口焊死）', async () => {
  const upload = vi.fn().mockResolvedValue({ id: 'a1', name: 'page-3.png' })
  const { pin, submit } = setup(upload as unknown as UploadAnnotation)

  await pin.send('这里不对')
  await pin.send('还有这里')

  expect(submit).toHaveBeenCalledTimes(2)
})
