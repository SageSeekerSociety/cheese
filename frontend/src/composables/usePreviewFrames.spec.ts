import { defineComponent, h } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import { usePreviewFrames } from './usePreviewFrames'

vi.mock('../lib/previewSession', () => ({ postPreviewSession: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(document, 'hidden')
})

// Independent review case: a clock/scheduler substitute, not browser sleep.
it('excludes frozen hidden time and cleans up the visible navigation budget', async () => {
  let clock = 0
  let hidden = false
  let tick: (() => void) | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => clock)
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  vi.stubGlobal('setInterval', (callback: () => void) => {
    tick = callback
    return 123
  })
  const clear = vi.fn()
  vi.stubGlobal('clearInterval', clear)
  const remove = vi.spyOn(document, 'removeEventListener')
  let host!: ReturnType<typeof usePreviewFrames>
  const { unmount } = render(
    defineComponent({
      setup() {
        host = usePreviewFrames('budget')
        return () => h('div')
      },
    })
  )
  await host.navigate(
    { url: 'https://content.example/_cheese/session', grant: 'review' },
    { url: 'https://content.example/', label: 'site/index.html', mime: 'text/html', version: 'v1', live: false },
    () => true
  )
  clock = 10_000
  tick!()
  hidden = true
  document.dispatchEvent(new Event('visibilitychange'))
  // No interval runs while the background page is frozen.
  clock = 90_000
  hidden = false
  document.dispatchEvent(new Event('visibilitychange'))
  tick!()
  expect(host.navigation.value).toBe('navigating')
  expect(host.error.value).toBe('')
  clock = 109_500
  tick!()
  expect(host.navigation.value).toBe('navigating')
  clock = 110_000
  tick!()
  expect(host.navigation.value).toBe('failed')
  expect(host.incoming.value).toBeNull()
  expect(clear).toHaveBeenCalledWith(123)
  expect(remove).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
  unmount()
  expect(host.frames.value).toEqual([])
})
