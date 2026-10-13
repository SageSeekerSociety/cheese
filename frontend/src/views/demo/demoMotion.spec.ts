// 动效台：慢放改的是产品自己那几条 `--dur-*` 的倍数，关掉是 0 —— 不另写一套时长。
//
// 这一份只测那几个纯函数，不挂页面。要守的是「**所有**时长 token 一起变」：只改
// 挑出来的几条，产品里就会有一半动一半不动；断言写成「样式表里每一条 `--dur-*` 都乘了
// 同一个倍数」，以后新加一条时长也自动被这一条盖住。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { applyMotion, MOTION_FACTOR, MOTION_MODES, prefersReducedMotion } from './demoMotion'

// `applyMotion` 读的是文档里真有的 `--dur-*`，所以先给文档摆上产品那四条时长
// （名字和值照抄 src/style.css 的 `:root`）和一条不是时长的。
const style = document.createElement('style')
style.textContent =
  ':root { --dur-press: 100ms; --dur-quick: 150ms; --dur-base: 220ms; --dur-slow: 320ms; --radius-md: 8px; }'
document.head.appendChild(style)

/** 每条断言都在自己新起的一块上量，互不干扰。 */
function box(): HTMLElement {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return el
}

afterEach(() => {
  document.documentElement.removeAttribute('style')
  delete document.documentElement.dataset.motion
  vi.unstubAllGlobals()
})

describe('动效档', () => {
  it('三档就是三个倍数：正常 ×1、慢放 ×4、关 ×0', () => {
    expect(MOTION_FACTOR).toEqual({ normal: 1, slow: 4, off: 0 })
    expect(MOTION_MODES.map((mode) => mode.label)).toEqual(['正常', '慢放 ×4', '关'])
  })

  it('慢放把每一条时长都乘 4，不是另写一套时长', () => {
    const el = box()
    applyMotion(el, 'slow')
    // 四条产品时长一条不漏，全都乘 4 —— 漏一条就会有一半动一半不动。
    expect(el.style.getPropertyValue('--dur-press')).toBe('400ms')
    expect(el.style.getPropertyValue('--dur-quick')).toBe('600ms')
    expect(el.style.getPropertyValue('--dur-base')).toBe('880ms')
    expect(el.style.getPropertyValue('--dur-slow')).toBe('1280ms')
    expect(el.dataset.motion).toBe('slow')
  })

  it('不是时长的 token 一律不动', () => {
    const el = box()
    applyMotion(el, 'slow')
    expect(el.style.getPropertyValue('--radius-md')).toBe('')
  })

  it('关掉是 0ms，切回正常就回到原值', () => {
    const el = box()
    applyMotion(el, 'off')
    expect(el.style.getPropertyValue('--dur-press')).toBe('0ms')
    expect(el.style.getPropertyValue('--dur-base')).toBe('0ms')
    expect(el.style.getPropertyValue('--dur-slow')).toBe('0ms')
    applyMotion(el, 'normal')
    expect(el.style.getPropertyValue('--dur-base')).toBe('220ms')
    expect(el.style.getPropertyValue('--dur-slow')).toBe('320ms')
    expect(el.dataset.motion).toBe('normal')
  })

  it('后来才加进样式表的那一条也被算进去（同一套倍数）', () => {
    const el = box()
    applyMotion(el, 'normal')
    const more = document.createElement('style')
    more.textContent = ':root { --dur-newly-added: 500ms; }'
    document.head.appendChild(more)
    applyMotion(el, 'slow')
    expect(el.style.getPropertyValue('--dur-newly-added')).toBe('2000ms')
    more.remove()
  })

  it('真页面那样改在文档根上：动效档落在 dataset 上，时长落在 inline 上', () => {
    applyMotion(document.documentElement, 'slow')
    expect(document.documentElement.dataset.motion).toBe('slow')
    expect(document.documentElement.style.getPropertyValue('--dur-base')).toBe('880ms')
  })
})

describe('系统的「减少动态效果」', () => {
  it('问了才答：系统说了少动就是少动', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduced-motion') }))
    expect(prefersReducedMotion()).toBe(true)
  })

  it('系统没这个设置时按没开算', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    expect(prefersReducedMotion()).toBe(false)
  })

  it('环境里根本没有 matchMedia 也不能炸', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(prefersReducedMotion()).toBe(false)
  })
})
