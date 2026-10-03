// 滚动的行为跟着系统的「减弱动效」偏好走：开着就 'auto'，关着就 'smooth'。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { scrollBehavior } from './motion'

function stubReducedMotion(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }))
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('scrollBehavior', () => {
  it('系统没开减弱动效时用 smooth', () => {
    stubReducedMotion(false)
    expect(scrollBehavior()).toBe('smooth')
  })

  it('系统开了减弱动效时用 auto', () => {
    stubReducedMotion(true)
    expect(scrollBehavior()).toBe('auto')
  })

  it('问的是 prefers-reduced-motion 这一条', () => {
    stubReducedMotion(false)
    scrollBehavior()
    expect(window.matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
  })

  it('没有 matchMedia 时按 auto 算，不抛异常', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(scrollBehavior()).toBe('auto')
  })
})
