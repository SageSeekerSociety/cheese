/**
 * 「窄」按这一格的边框宽度判，不按内容盒。
 *
 * 被量的这一格自己会按判定结果收内边距（`.pv--narrow`、`.is-narrow` 都是 16px→8px），
 * 内容盒因此比边框盒窄出这 16px：按内容盒判，宽度落在临界那 16px 里就会窄→宽→窄来回翻，
 * ResizeObserver 一路回调、画面不停重排。这条把两种盒子给出的数分开喂，钉住用的是边框盒。
 */
import { ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'

import { NARROW_PX, usePreviewNarrow } from './previewNarrow'

let deliver: ResizeObserverCallback | null = null
afterEach(() => {
  deliver = null
  vi.unstubAllGlobals()
})
function stubObserver() {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {
        deliver = callback
      }
      observe() {}
      disconnect() {}
    }
  )
}
function entry(contentWidth: number, borderWidth: number) {
  return {
    contentBoxSize: [{ inlineSize: contentWidth, blockSize: 100 }],
    borderBoxSize: [{ inlineSize: borderWidth, blockSize: 100 }],
    contentRect: { width: contentWidth, height: 100 },
  } as unknown as ResizeObserverEntry
}

it('边框宽过了线就是宽的，哪怕内容盒因为内边距还窄着', async () => {
  stubObserver()
  const el = document.createElement('div')
  const { narrow, measured } = usePreviewNarrow(ref(el))
  await Promise.resolve()
  expect(deliver).not.toBeNull()

  // 580 的格子：宽处内边距 16px 时内容盒只有 548（< 560），窄处 8px 时是 564（>= 560）。
  // 判据看的是 580 本身，不随内边距翻。
  deliver!([entry(NARROW_PX - 12, NARROW_PX + 20)], {} as ResizeObserver)
  await Promise.resolve()
  expect(measured.value).toBe(true)
  expect(narrow.value).toBe(false)

  deliver!([entry(NARROW_PX - 40, NARROW_PX - 20)], {} as ResizeObserver)
  await Promise.resolve()
  expect(narrow.value).toBe(true)
})
