// 现场圆点分级 + display-time translation: the platform rule reads the
// structured fields the backend persisted — never guessed from natural
// language.
import { afterAll, beforeEach, describe, expect, it } from 'vitest'

import { isPlatformEvent, TOOL_LABELS, toolLabel } from './toolLabels'

import { setLocale } from '@/i18n'
import en from '@/i18n/messages/en/toolLabels.json'
import zhCN from '@/i18n/messages/zh-CN/toolLabels.json'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

// The table holds catalog keys now, so every assertion below depends on which
// locale is active. happy-dom reports navigator.language as en-US; without
// pinning the language first, the Chinese cases would get English and fail
// looking like a broken extraction (same reason as lib/board.spec.ts).
beforeEach(() => setLocale('zh-CN'))

describe('toolLabel (zh-CN)', () => {
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

// The same table, read in English. Every case here has a twin above: a row that
// changes word in one language and not the other is exactly what the catalog
// exists to prevent, and only a second-language assertion can see it.
describe('toolLabel (en)', () => {
  beforeEach(() => setLocale('en'))
  // The locale is module-level state. Leaving it on `en` would leak into
  // whatever runs next in this file, so put it back when this block ends.
  afterAll(() => setLocale('zh-CN'))

  it('names the native tools with the same short verbs', () => {
    expect(toolLabel('Glob')).toBe('Find file')
    expect(toolLabel('Grep')).toBe('Search content')
    expect(toolLabel('Agent')).toBe('Spawn an agent')
    expect(toolLabel('Task')).toBe('Spawn an agent')
  })

  it('gives the pi tools the same words as their Claude Code twins', () => {
    expect(toolLabel('bash')).toBe('Run command')
    expect(toolLabel('read')).toBe('Read file')
    expect(toolLabel('edit')).toBe('Edit file')
    expect(toolLabel('grep')).toBe('Search content')
  })

  it('names the platform commands', () => {
    expect(toolLabel('cheese_accept_request')).toBe('Submit an acceptance card')
    expect(toolLabel('cheese_doc_set')).toBe('Update the topic doc')
    expect(toolLabel('chat_send')).toBe('Publish a message')
    expect(toolLabel('cheese_chat_send')).toBe('Publish a message')
  })

  it('names the background jobs', () => {
    expect(toolLabel('bash_start')).toBe('Start a background task')
    expect(toolLabel('bash_kill')).toBe('Stop the background task')
  })

  it('strips the mcp__cheese__ prefix and falls back to the raw name too', () => {
    expect(toolLabel('mcp__cheese__update_doc')).toBe('Update the topic doc')
    expect(toolLabel('FutureTool')).toBe('FutureTool')
  })

  // 这一条是这张表的**第二道闸门**。表里存的是键，而「表指了、目录里却没有」这件事
  // 谁都不问：`catalog.spec.ts` 问的是「目录里的键有没有源码引用」，方向正好相反，
  // 而 `t(key)` 传的是变量，那个「字面调用」检查也够不着。漏一条的后果是现场那一行
  // 当场渲染出 `toolLabels.someStep` —— 比漏翻更难认。
  it('每一个工具名的键在英文词表里都真的有词条', () => {
    const keys = new Set(Object.values(TOOL_LABELS).map((key) => key.replace(/^toolLabels\./, '')))
    const orphans = [...keys].filter((key) => !(key in en) || !(key in zhCN)).sort()
    expect(orphans, `表里指了、词表里没有：${orphans.join(', ')}`).toEqual([])
    // 反过来也看一次：目录里有、表里没人指的键是抽到一半留下的半成品。
    const unclaimed = Object.keys(en)
      .filter((key) => !keys.has(key))
      .sort()
    expect(unclaimed, `词表里有、表里没人指：${unclaimed.join(', ')}`).toEqual([])
  })

  // 键存在不等于翻过。少一条英文时 vue-i18n 会静默回落到中文——现场那一行于是
  // 在一个全英文的界面上冒出一个中文动词。逐条走一遍表，比挑几条断言能看见的更多。
  it('每一个工具名在英文下都是英文，既不掉回中文也不漏出键名', () => {
    for (const [name, key] of Object.entries(TOOL_LABELS)) {
      const said = toolLabel(name)
      expect(said, `${name} → ${key} 没有英文`).not.toContain('toolLabels.')
      expect(CJK.test(said), `${name} → ${key} 回落成了中文：${said}`).toBe(false)
    }
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
