// 现场圆点分级 + display-time translation: the platform rule reads the
// structured fields the backend persisted — never guessed from natural
// language.
import { describe, expect, it } from 'vitest'

import { isPlatformEvent, toolLabel } from './toolLabels'

describe('toolLabel', () => {
  it('translates native tools (incl. the ones that used to leak raw)', () => {
    expect(toolLabel('Glob')).toBe('查找文件')
    expect(toolLabel('Grep')).toBe('搜索内容')
    expect(toolLabel('Agent')).toBe('派出分身')
    expect(toolLabel('Task')).toBe('派出分身')
  })

  it('strips the mcp__cheese__ prefix and falls back to the raw name', () => {
    expect(toolLabel('mcp__cheese__update_doc')).toBe('更新文档')
    expect(toolLabel('FutureTool')).toBe('FutureTool')
  })
})

describe('isPlatformEvent (persisted event blocks)', () => {
  it('trusts the structured meta.platform flag', () => {
    expect(isPlatformEvent({ tool: 'Bash', platform: true }, [])).toBe(true)
    expect(isPlatformEvent({ tool: 'Grep', platform: false }, [])).toBe(false)
  })

  it('falls back to the action: refs tag for pre-meta action cards', () => {
    expect(isPlatformEvent(undefined, ['action:doc'])).toBe(true)
    expect(isPlatformEvent(null, ['some-block-id'])).toBe(false)
  })

  it('stays neutral when neither structured signal exists', () => {
    expect(isPlatformEvent(undefined, undefined)).toBe(false)
    expect(isPlatformEvent(null, [])).toBe(false)
  })
})
