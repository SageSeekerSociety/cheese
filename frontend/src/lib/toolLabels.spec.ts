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

  it('translates the platform tools every harness calls by the same name', () => {
    expect(toolLabel('mcp__native__cheese_accept_request')).toBe('提交审阅')
    expect(toolLabel('cheese_doc_set')).toBe('更新文档')
    expect(toolLabel('chat_send')).toBe('发布消息')
    expect(toolLabel('cheese_worktree')).toBe('准备工作目录')
  })

  it('translates the background jobs, which pi has none of on its own', () => {
    expect(toolLabel('bash_start')).toBe('启动后台任务')
    expect(toolLabel('bash_kill')).toBe('终止后台任务')
  })

  it('strips the MCP prefix of whichever server published the tool', () => {
    // 服务器注册名是 `native`，所以真实行长的就是这个样子。
    expect(toolLabel('mcp__native__update_doc')).toBe('更新文档')
    // 旧拼法仍然认 —— 历史行还躺在库里。
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
