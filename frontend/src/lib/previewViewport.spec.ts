/**
 * 三档视口的宽度与缩放：平板档是**真的 768px**（按平板排版），面板装不下时整块等比缩小
 * 也还是 768；装得下就是原尺寸。这条守着的是「说自己在看平板、其实看的是右栏」那个坑：
 * 直接把 iframe 压到面板宽，页面的 `@media` 按面板而不是平板命中，量出来的圈选坐标也
 * 跟着错。
 */
import { expect, it } from 'vitest'

import { defaultViewport, fitScale, PHONE_ONLY_PX, VIEWPORT_WIDTH } from './previewViewport'

it('桌面档没有固定宽度，平板 768、手机 375', () => {
  expect(VIEWPORT_WIDTH.desktop).toBeNull()
  expect(VIEWPORT_WIDTH.tablet).toBe(768)
  expect(VIEWPORT_WIDTH.mobile).toBe(375)
})

it('面板比那一档宽时按 1:1 画，放不下才等比缩小', () => {
  expect(fitScale(1200, 'tablet')).toBe(1)
  expect(fitScale(768, 'tablet')).toBe(1)
  expect(fitScale(600, 'tablet')).toBeCloseTo(0.78125, 5)
  expect(fitScale(375, 'mobile')).toBe(1)
  expect(fitScale(300, 'mobile')).toBe(0.8)
})

it('桌面档永远不缩：它本来就等于面板自己的宽度', () => {
  expect(fitScale(200, 'desktop')).toBe(1)
})

it('还没量到宽度（0）时不缩，也别拿它算默认档', () => {
  expect(fitScale(0, 'tablet')).toBe(1)
  expect(defaultViewport(0)).toBe('desktop')
})

it('窄到手机上默认按手机档，其余按桌面档', () => {
  expect(defaultViewport(PHONE_ONLY_PX - 1)).toBe('mobile')
  expect(defaultViewport(PHONE_ONLY_PX)).toBe('desktop')
  expect(defaultViewport(390)).toBe('mobile')
  expect(defaultViewport(1440)).toBe('desktop')
})
