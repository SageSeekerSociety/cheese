import { defineComponent, h } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import { t } from '../i18n'

import { APP_NAVIGATION_BUDGET_MS, navigationBudget, navigationTier, usePreviewFrames } from './usePreviewFrames'

vi.mock('../lib/previewSession', () => ({ postPreviewSession: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(document, 'hidden')
})

/** A clock/scheduler substitute, not browser sleep: the test decides how much time
 *  passed and when the interval fires. */
function fixture() {
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
  return {
    host,
    clear,
    remove,
    unmount,
    at(ms: number) {
      clock = ms
      tick!()
    },
    hide() {
      hidden = true
      document.dispatchEvent(new Event('visibilitychange'))
    },
    show() {
      hidden = false
      document.dispatchEvent(new Event('visibilitychange'))
    },
    navigate(page: { budgetMs?: number }) {
      return host.navigate(
        { url: 'https://content.example/_cheese/session', grant: 'review' },
        {
          url: 'https://content.example/',
          label: 'site/index.html',
          mime: 'text/html',
          version: 'v1',
          live: false,
          ...page,
        },
        () => true
      )
    },
  }
}

it('falls back to the file budget and clamps to the ten-minute ceiling', () => {
  for (const value of [undefined, 0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
    expect(navigationTier(value)).toBe(30_000)
    expect(navigationBudget(value)).toBe(30_000)
  }
  expect(navigationTier(APP_NAVIGATION_BUDGET_MS)).toBe(130_000)
  expect(navigationBudget(APP_NAVIGATION_BUDGET_MS)).toBe(132_000)
  expect(navigationTier(1e9)).toBe(600_000)
  expect(navigationBudget(1e9)).toBe(602_000)
})

it('gives an app the longer budget, not the file one', async () => {
  const test = fixture()
  await test.navigate({ budgetMs: APP_NAVIGATION_BUDGET_MS })
  // Past the file budget, still inside the app one.
  test.at(31_000)
  expect(test.host.navigation.value).toBe('navigating')
  test.at(131_500)
  expect(test.host.navigation.value).toBe('navigating')
  test.at(132_000)
  expect(test.host.navigation.value).toBe('failed')
  // 文案说的是档位，不是计时器里那个多了 2 秒的数。
  expect(test.host.error.value).toBe(t('work.room.preview.navigationTimeout', { seconds: 130 }))
})

it('excludes frozen hidden time and cleans up the visible navigation budget', async () => {
  const test = fixture()
  await test.navigate({})
  test.at(10_000)
  test.hide()
  // No interval runs while the background page is frozen.
  test.at(90_000)
  test.show()
  test.at(90_000)
  expect(test.host.navigation.value).toBe('navigating')
  expect(test.host.error.value).toBe('')
  test.at(109_500)
  expect(test.host.navigation.value).toBe('navigating')
  test.at(110_000)
  expect(test.host.navigation.value).toBe('failed')
  expect(test.host.incoming.value).toBeNull()
  expect(test.clear).toHaveBeenCalledWith(123)
  expect(test.remove).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
  test.unmount()
  expect(test.host.frames.value).toEqual([])
})
