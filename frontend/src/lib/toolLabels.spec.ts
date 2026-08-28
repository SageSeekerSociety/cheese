// 现场圆点分级 + display-time translation: the platform rule is deterministic
// (tool-name prefix / literal `cheese <sub>` in a Bash command) — never
// guessed from natural language.
import { describe, expect, it } from 'vitest'

import { formatToolAction, isPlatformAction, isPlatformEvent, toolLabel } from './toolLabels'

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

describe('isPlatformAction (live worklog dots)', () => {
  it('flags cheese MCP tools, prefixed or short-named', () => {
    expect(isPlatformAction('mcp__cheese__update_doc', {})).toBe(true)
    expect(isPlatformAction('update_doc', {})).toBe(true)
    expect(isPlatformAction('remember', { fact: 'x' })).toBe(true)
  })

  it('flags Bash commands invoking the cheese CLI', () => {
    expect(isPlatformAction('Bash', { command: 'cheese title "新标题"' })).toBe(true)
    expect(isPlatformAction('Bash', { command: '/usr/local/bin/cheese doc set' })).toBe(true)
  })

  it('keeps plain work neutral', () => {
    expect(isPlatformAction('Bash', { command: 'ls -la' })).toBe(false)
    expect(isPlatformAction('Bash', { command: 'echo cheese' })).toBe(false)
    expect(isPlatformAction('Bash', {})).toBe(false)
    expect(isPlatformAction('Grep', { pattern: 'cheese title' })).toBe(false)
    expect(isPlatformAction('Read', { file_path: '/a/cheese title.txt' })).toBe(false)
    expect(isPlatformAction('Agent', { description: '查 cheese 用法' })).toBe(false)
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

describe('formatToolAction', () => {
  it('renders verb · preview from the live tool input', () => {
    expect(formatToolAction('Grep', { pattern: 'TODO' })).toBe('搜索内容 · TODO')
    expect(formatToolAction('mcp__cheese__notify', { title: '进展' })).toBe('发送通知 · 进展')
  })

  it('renders the bare verb when the preview arg is missing', () => {
    expect(formatToolAction('Grep', null)).toBe('搜索内容')
    expect(formatToolAction('FutureTool', { x: 1 })).toBe('FutureTool')
  })
})
