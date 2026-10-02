import { describe, expect, it } from 'vitest'

import { TOOLBAR_COMPACT_WIDTH, TOOLBAR_FULL_WIDTH, TOOLBAR_MINIMAL_WIDTH, toolbarTier } from './designToolbar'

describe('工具栏分档', () => {
  it('阈值照参考物定的 600 / 224 / 150', () => {
    expect(TOOLBAR_FULL_WIDTH).toBe(600)
    expect(TOOLBAR_COMPACT_WIDTH).toBe(224)
    expect(TOOLBAR_MINIMAL_WIDTH).toBe(150)
  })

  it('宽的时候 full，往下依次 compact / minimal / concealed', () => {
    expect(toolbarTier(1000)).toBe('full')
    expect(toolbarTier(600)).toBe('full')
    expect(toolbarTier(599)).toBe('compact')
    expect(toolbarTier(224)).toBe('compact')
    expect(toolbarTier(223)).toBe('minimal')
    expect(toolbarTier(150)).toBe('minimal')
    expect(toolbarTier(149)).toBe('concealed')
    expect(toolbarTier(0)).toBe('full')
  })

  it('量不到宽度（隐藏、还没布局、NaN）时按最宽处理，不收起工具', () => {
    expect(toolbarTier(Number.NaN)).toBe('full')
    expect(toolbarTier(Number.POSITIVE_INFINITY)).toBe('full')
    expect(toolbarTier(-1)).toBe('full')
  })
})
