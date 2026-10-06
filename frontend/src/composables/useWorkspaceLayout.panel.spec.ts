// 话题页右侧面板按主区宽度分三档：放得下两栏就并排，宽到对话还有余地时默认开着；
// 放不下两栏就浮在对话上方。
import { describe, expect, it } from 'vitest'

import { panelMode, panelOpenByDefault } from './useWorkspaceLayout'

describe('右侧面板三档', () => {
  it('放得下两栏时并排，放不下时浮在对话上方', () => {
    expect(panelMode(1300)).toBe('docked')
    expect(panelMode(900)).toBe('docked')
    expect(panelMode(700)).toBe('float')
  })

  it('宽屏默认开着，笔记本宽度默认收着', () => {
    expect(panelOpenByDefault(1300)).toBe(true)
    expect(panelOpenByDefault(900)).toBe(false)
  })

  it('还没量出宽度时按并排开着算，不先闪一下浮层', () => {
    expect(panelMode(0)).toBe('docked')
    expect(panelOpenByDefault(0)).toBe(true)
  })
})
