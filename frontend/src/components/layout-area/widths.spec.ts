// A panel's own width: one number per key, shared by every area that places the panel,
// never outside its bounds, and the initial width when nothing usable was stored.
import type { LayoutPanelWidth } from './types'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { readWidth, rememberWidth } from './widths'

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

  it('a broken stored value gives the initial width', () => {
    localStorage.setItem('test.sidebarWidth', 'wide')
    expect(readWidth(SIDEBAR)).toBe(280)
  })
})
