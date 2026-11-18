// A panel's own width: one number per key, shared by every area that places the panel,
// never outside its bounds, and the initial width when nothing usable was stored.
import type { LayoutPanelWidth } from './types'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { readWidth, rememberedFold, rememberedWidth, rememberFold, rememberWidth } from './widths'

const SIDEBAR: LayoutPanelWidth = { initial: 280, min: 190, max: 480, storageKey: 'test.sidebarWidth' }

describe('a panel width remembered under its key', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => localStorage.clear())

  it('starts at the initial width', () => {
    expect(readWidth(SIDEBAR)).toBe(280)
  })

  it('opens at the width it was dragged to, wherever else the panel is placed', () => {
    rememberWidth(SIDEBAR, 333)
    expect(readWidth({ ...SIDEBAR })).toBe(333)
  })

  it('cannot be dragged or stored outside its bounds', () => {
    expect(rememberWidth(SIDEBAR, 20)).toBe(190)
    expect(readWidth(SIDEBAR)).toBe(190)
    localStorage.setItem('test.sidebarWidth', '5000')
    expect(readWidth(SIDEBAR)).toBe(480)
  })

  it('a width dragged in one place is the width every other reader shows at once', () => {
    const DRAWER: LayoutPanelWidth = { ...SIDEBAR, storageKey: 'test.sharedWidth' }
    const cell = rememberedWidth(DRAWER)
    const drawer = rememberedWidth({ ...DRAWER })
    rememberWidth(DRAWER, 400, false)
    expect(cell.value).toBe(400)
    expect(drawer.value).toBe(400)
    expect(readWidth(DRAWER)).toBe(280)
    rememberWidth(DRAWER, 400)
    expect(readWidth(DRAWER)).toBe(400)
  })

  it('a fold is the same for every reader of its key, and remembered', () => {
    const one = rememberedFold('test.folded')
    const other = rememberedFold('test.folded')
    expect(one.value).toBe(false)
    rememberFold('test.folded', true)
    expect(other.value).toBe(true)
    expect(localStorage.getItem('test.folded')).toBe('1')
  })

  it('a broken stored value gives the initial width', () => {
    localStorage.setItem('test.sidebarWidth', 'wide')
    expect(readWidth(SIDEBAR)).toBe(280)
  })
})
