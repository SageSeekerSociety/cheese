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

:::chart line
| 周 | 改版前(次) | 改版后(次) |
|---|---|---|
| 第 1 周 | 1,020 | 1,980 |
| 第 2 周 | 1,060 | 2,150 |
:::

\`\`\`mermaid
flowchart TD
  A[提交] --> B{检查通过?}
  B -->|是| C[合并]
  B -->|否| D[退回]
\`\`\`

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

  it('refuses a chart of a type the document does not draw, naming its line', () => {
    expect(checkMarkdownWrite('正文\n\n:::chart radar\n| a | b |\n|---|---|\n| x | 1 |\n:::\n')?.line).toBe(3)
  })

  it('refuses horizontal on a chart that has no bars', () => {
    expect(checkMarkdownWrite(':::chart pie horizontal\n| a | b |\n|---|---|\n| x | 1 |\n:::\n')).not.toBeNull()
  })

  it('refuses a chart that holds something besides its table', () => {
    expect(checkMarkdownWrite(':::chart bar\n说明\n\n| a | b |\n|---|---|\n| x | 1 |\n:::\n')).not.toBeNull()
  })

  it('refuses words where a chart needs a number, naming the row', () => {
    expect(
      checkMarkdownWrite(':::chart bar\n| 周 | 次数 |\n|---|---|\n| 一 | 10 |\n| 二 | 约 20 次 |\n:::\n')?.line
    ).toBe(5)
  })

  it('refuses a pie with more than one series', () => {
    expect(checkMarkdownWrite(':::chart pie\n| a | b | c |\n|---|---|---|\n| x | 1 | 2 |\n:::\n')).not.toBeNull()
  })

  it('accepts numbers written with separators, signs, percent and currency, and empty cells', () => {
    const md =
      ':::chart bar\n| 月 | 收入 | 增长 |\n|---|---|---|\n| 一 | $1,274 | +12% |\n| 二 | ¥3.5 | |\n| 三 | 900 | -1.3 |\n:::\n'
    expect(checkMarkdownWrite(md)).toBeNull()
  })

  it.each(['pie title 花费\n  "云" : 410', 'xychart-beta\n  bar [1, 2]', 'timeline\n  2024 : 上线'])(
    'refuses a mermaid diagram the document has its own block for: %s',
    (diagram) => {
      expect(checkMarkdownWrite(`正文\n\n\`\`\`mermaid\n${diagram}\n\`\`\`\n`)?.line).toBe(4)
    }
  )

  it('accepts the other mermaid diagrams', () => {
    expect(checkMarkdownWrite('```mermaid\n%% 注释\nerDiagram\n  A ||--o{ B : has\n```\n')).toBeNull()
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
