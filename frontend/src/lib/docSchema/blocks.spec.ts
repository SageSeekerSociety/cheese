// The blocks the writing guide teaches must come back exactly as written: the
// guide shows 芝士 one spelling, an edit has to quote the document's text byte
// for byte, and a document that respells itself on every save records a
// version nobody made.
import { describe, expect, it } from 'vitest'

import { nodeMarkdown, parseMarkdown } from '.'

const roundTrip = (md: string) => nodeMarkdown(parseMarkdown(md))

/** The node types a Markdown document parses into, depth first. */
function types(md: string): string[] {
  const out: string[] = []
  parseMarkdown(md).descendants((node) => {
    out.push(node.type.name)
    return true
  })
  return out
}

function marks(md: string): string[] {
  const out: string[] = []
  parseMarkdown(md).descendants((node) => {
    for (const mark of node.marks) out.push(`${mark.type.name}:${String(mark.attrs.kind ?? '')}:${node.text ?? ''}`)
    return true
  })
  return out
}

describe('the spelling the writing guide teaches comes back unchanged', () => {
  it.each([
    ['callout', '> [!IMPORTANT]\n> 推荐方案 A：现成的扩展最多，一个月能上线。'],
    ['callout with two paragraphs', '> [!WARNING]\n> 第一段。\n>\n> 第二段。'],
    [
      'timeline',
      ':::timeline\n- 10:02 | 提交合并请求 {✓ 单元测试} {✗ 类型检查}\n  类型检查报了 2 处错误。\n- 10:15 | 修好后重新提交 {! 等待评审}\n:::',
    ],
    ['stats', ':::stats\n- 活跃项目 | 128 | +12%\n- 首字时间 | 6.1 秒\n:::'],
    [
      'columns',
      '::::columns\n:::column\n**方案 A**\n\n一个月上线。\n:::\n:::column\n**方案 B**\n\n两周上线。\n:::\n::::',
    ],
    ['details', '<details><summary>原始数据</summary>\n\n- 一\n- 二\n\n</details>'],
    ['status in prose', '这一项 {✓ 通过}，那一项 {✗ 不发}，还有 {! 待定}。'],
    ['inline formula', '面积是 $\\pi r^2$。'],
    ['block formula', '$$\nE = mc^2\n$$'],
    ['footnote', '花费占 59.8%[^2]。\n\n[^2]: (410 + 352) ÷ 1,274 ≈ 59.8%'],
    [
      'chart',
      ':::chart line\n| 周     | 改版前(次) | 改版后(次) |\n| ----- | ------ | ------ |\n| 第 1 周 | 1,020  | 1,980  |\n:::',
    ],
    ['horizontal chart', ':::chart bar horizontal\n| 渠道  | 用户  |\n| --- | --- |\n| 搜索  | 410 |\n:::'],
  ])('%s', (_name, md) => {
    expect(roundTrip(md)).toBe(md)
  })
})

describe('reading what people actually write', () => {
  it('reads a chart around a table written without padding as the same chart', () => {
    expect(types(':::chart pie\n| 渠道 | 用户 |\n|---|---|\n| 搜索 | 410 |\n:::').slice(0, 2)).toEqual([
      'chart',
      'table',
    ])
  })

  it('leaves a chart of an unknown type as text, so nothing in it is lost', () => {
    expect(types(':::chart radar\n| a | b |\n|---|---|\n| x | 1 |\n:::')).not.toContain('chart')
  })

  it('reads a stat card written without the leading dash', () => {
    expect(roundTrip(':::stats\n活跃项目 | 128 | +12%\n:::')).toBe(':::stats\n- 活跃项目 | 128 | +12%\n:::')
  })

  it('reads an alert written in lower case as the same callout', () => {
    expect(types('> [!note]\n> 补充。')).toContain('callout')
  })

  it('keeps an ordinary quote a quote', () => {
    expect(types('> 只是引用。')).toEqual(['blockquote', 'paragraph', 'text'])
  })

  it('keeps a stat card and a timeline inside a column', () => {
    const md =
      '::::columns\n:::column\n:::stats\n- 甲 | 1\n:::\n:::\n:::column\n:::timeline\n- 周一 | 开始\n:::\n:::\n::::'
    expect(roundTrip(md)).toBe(md)
  })

  it('takes a dollar before a number as money, not a formula', () => {
    for (const md of ['九月花费 $1,274，比预算高 $74。', '价格从 $5 到 $10，或者 $5~$10。']) {
      expect(types(md)).not.toContain('mathInline')
      expect(roundTrip(md)).toBe(md)
    }
  })

  it('reads an inline formula that starts with a digit', () => {
    for (const md of [
      '第一行变成 $1 - \\sum_{k=m}^{n+m-1} \\binom{n+m-1}{k} q^k p^{n+m-1-k}$。',
      '满足 $0 \\le k \\le n-1$ 且总和为 $1$，乘上 $1 = (p + q)^{n-1-k}$。',
      '复杂度是 $2^n$，耗时 $1 \\text{ 秒}$。',
    ]) {
      expect(types(md)).toContain('mathInline')
      expect(roundTrip(md)).toBe(md)
    }
  })

  it('keeps money as text when a real formula or code span follows on the same line', () => {
    for (const md of [
      '花费 $100，其中 $x$ 是单价。',
      '花费 $100，其中$x$是单价。',
      '立减 $50（即$x$折），或者 $50（$x$折）。',
      'It costs $100 ($x$ per unit) or $100 for ($x$ items).',
    ]) {
      expect(types(md).filter((t) => t === 'mathInline')).toEqual(
        md.includes('或者') || md.includes(' or ') ? ['mathInline', 'mathInline'] : ['mathInline']
      )
      expect(roundTrip(md)).toBe(md)
    }
    const withCode = '花费 $100，配置在 `$HOME` 目录下。'
    expect(types(withCode)).not.toContain('mathInline')
    expect(roundTrip(withCode)).toBe(withCode)
  })

  it('keeps an escaped dollar inside an inline formula', () => {
    const md = '记价格函数为 $f(a) = \\$a + \\$5$。'
    expect(types(md).filter((t) => t === 'mathInline')).toEqual(['mathInline'])
    expect(roundTrip(md)).toBe(md)
  })

  it('reads inline double-dollar formulas and does not merge across $$', () => {
    expect(roundTrip('由 $$a + b = c$$ 和 $$ x = 1 $$ 可知。')).toBe('由 $a + b = c$ 和 $x = 1$ 可知。')
    expect(roundTrip('$$a = 1$$ 和 $$b = 2$$')).toBe('$a = 1$ 和 $b = 2$')
    const mixed = '$$a = 1$$ 是恒等式：\n\n$$\nb = 2\n$$'
    expect(types(mixed).filter((t) => t.startsWith('math'))).toEqual(['mathInline', 'mathBlock'])
  })

  it('reads every status kind and leaves the words as text', () => {
    expect(marks('{✓ 通过} {✗ 不发} {! 待定}')).toEqual(['status:ok:通过', 'status:no:不发', 'status:warn:待定'])
  })

  it('keeps formatting inside a status tag', () => {
    const once = roundTrip('{✓ **已复核**}')
    expect(marks(once)).toEqual(marks('{✓ **已复核**}'))
    expect(roundTrip(once)).toBe(once)
  })

  it('reads a status tag in a table cell', () => {
    expect(marks('| 项 | 结果 |\n| --- | --- |\n| 单测 | {✓ 通过} |')).toEqual(['status:ok:通过'])
  })

  it('leaves braces that are not a status tag alone', () => {
    expect(marks('配置写成 {a: 1}')).toEqual([])
  })
})
