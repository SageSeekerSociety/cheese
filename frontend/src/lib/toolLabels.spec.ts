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

  it('translates the pi tools, whose names are another set entirely', () => {
    expect(toolLabel('bash')).toBe('执行命令')
    expect(toolLabel('read')).toBe('读取文件')
    expect(toolLabel('edit')).toBe('修改文件')
    expect(toolLabel('grep')).toBe('搜索内容')
  })

  it('translates the platform commands a pi room calls as tools', () => {
    // 同一件事在两个 harness 里叫不同的名字：一边是 MCP 工具，一边是 CLI 的
    // 命令树生成的目录。两边都得有词，否则现场那一行显示的是它内部的拼法。
    expect(toolLabel('cheese_accept_request')).toBe('提交验收卡')
    expect(toolLabel('cheese_doc_set')).toBe('更新实况文档')
    expect(toolLabel('chat_send')).toBe('发布消息')
    expect(toolLabel('cheese_chat_send')).toBe('发布消息')
  })

  it('translates the background jobs, which pi has none of on its own', () => {
    expect(toolLabel('bash_start')).toBe('启动后台任务')
    expect(toolLabel('bash_kill')).toBe('终止后台任务')
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
