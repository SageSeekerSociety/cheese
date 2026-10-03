// A write is refused when a reader would lose something: text that does not
// make it into the document, or a block written in a shape that reads as
// something else. Everything the writing guide teaches is accepted as written.
import { describe, expect, it } from 'vitest'

import { checkMarkdownWrite } from './writeCheck'

const GUIDE = `# 搜索框改版复盘

> [!IMPORTANT]
> 搜索框改成常驻后，搜索次数翻倍[^1]，用户找到结果的比例没变：建议保留。

:::stats
- 日均搜索 | 2,140 次 | +104%
- 找到结果 | 71% | 持平
:::

:::timeline
- 10:02 | 提交合并请求 {✓ 单元测试} {✗ 类型检查}
  类型检查报了 2 处错误。
- 10:15 | 修好后重新提交 {! 等待评审}
:::

::::columns
:::column
**方案 A**

一个月上线。
:::
:::column
**方案 B**

两周上线，面积是 $\\pi r^2$。
:::
::::

<details><summary>原始数据</summary>

$$
E = mc^2
$$

</details>

九月花费 $1,274。

[^1]: 改版前日均 1,049 次：2,140 ÷ 1,049 − 1 ≈ 104%。
`

describe('what the writing guide teaches', () => {
  it('is accepted as written', () => {
    expect(checkMarkdownWrite(GUIDE)).toBeNull()
  })
})

describe('a block in the wrong shape', () => {
  it('refuses a container with no closing line, naming its line', () => {
    expect(checkMarkdownWrite('正文\n\n:::timeline\n- 周一 | 开始\n')?.line).toBe(3)
  })

  it('refuses a container the document does not have', () => {
    expect(checkMarkdownWrite(':::mermaid\ngraph TD\n:::\n')).not.toBeNull()
  })

  it('refuses a timeline item without its separator', () => {
    expect(checkMarkdownWrite(':::timeline\n- 周一 | 开始\n- 周二 结束\n:::\n')?.line).toBe(3)
  })

  it('refuses a stat card without its separator', () => {
    expect(checkMarkdownWrite(':::stats\n- 活跃项目 128\n:::\n')?.line).toBe(2)
  })

  it('refuses columns that are not two or three', () => {
    expect(checkMarkdownWrite('::::columns\n:::column\n只有一栏\n:::\n::::\n')).not.toBeNull()
  })

  it('refuses a footnote with no definition', () => {
    expect(checkMarkdownWrite('花费占 59.8%[^2]。\n')?.line).toBe(1)
  })
})

describe('text that is not a block', () => {
  it('accepts money and braces as plain text', () => {
    expect(checkMarkdownWrite('预算 $5 到 $10，配置 {a: 1}。\n')).toBeNull()
  })

  it('accepts colons and footnote-looking text inside code', () => {
    expect(checkMarkdownWrite('```\n:::timeline\n[^1]\n```\n')).toBeNull()
  })
})
