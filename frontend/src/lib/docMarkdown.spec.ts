// @vitest-environment jsdom
// Living-doc round-trip corpus (军规 1: never silently drop content).
//
// Every case asserts: parse(markdown) → serialize ≡ original under the
// documented normalization rules (see docMarkdown.ts). A failing case here is
// syntax the visual editor would corrupt on save — either fix the editor
// config or make sure the lossy-load banner covers it.
import { Editor } from '@tiptap/core'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { compareRoundTrip, docExtensions, normalizeMarkdown, serializeDoc } from './docMarkdown'

let editor: Editor

beforeAll(() => {
  editor = new Editor({
    element: document.createElement('div'),
    extensions: docExtensions(),
    content: '',
  })
})

afterAll(() => {
  editor.destroy()
})

function roundTrip(md: string): string {
  editor.commands.setContent(md, { contentType: 'markdown' })
  return serializeDoc(editor)
}

function expectClean(md: string) {
  const rt = roundTrip(md)
  const report = compareRoundTrip(md, rt)
  if (!report.clean) {
    // Show the raw round trip too — the normalized diff alone can be terse.
    console.error('--- original ---\n' + md + '\n--- round-trip ---\n' + rt)
  }
  expect(report.diff).toBe('')
  expect(report.clean).toBe(true)
}

describe('normalizeMarkdown tolerances', () => {
  it('collapses blank-line runs and trailing whitespace', () => {
    expect(normalizeMarkdown('a  \n\n\n\nb\n')).toBe('a\n\nb')
  })
  it('normalizes table padding and separator dashes', () => {
    expect(normalizeMarkdown('| a  |  b |\n|----|:---:|')).toBe('| a | b |\n| --- | :---: |')
  })
  it('normalizes bullet markers to -', () => {
    expect(normalizeMarkdown('* one\n+ two')).toBe('- one\n- two')
  })
  it('normalizes blockquote markers', () => {
    expect(normalizeMarkdown('>a\n> > b')).toBe('> a\n> > b')
  })
  it('keeps fenced code byte-exact', () => {
    const md = '```py\nx = 1   \n\n\ny = 2\n```'
    expect(normalizeMarkdown(md)).toBe(md)
  })
})

describe('round-trip corpus', () => {
  it('headings 1-4', () => {
    expectClean('# 一级标题\n\n## 二级 Heading\n\n### 三级\n\n#### 四级标题')
  })

  it('bold / italic / strike / inline code', () => {
    expectClean('正文有 **粗体** 和 *斜体*，还有 ~~删除线~~ 与 `inline_code()` 混排。')
  })

  it('code block with language tag', () => {
    expectClean('```python\ndef hello(name: str) -> str:\n    return f"hi {name}"\n```')
  })

  it('code block without language', () => {
    expectClean('```\nplain text block\n  with indentation kept\n```')
  })

  it('unordered list', () => {
    expectClean('- 第一项\n- 第二项 with English\n- 第三项')
  })

  it('ordered list', () => {
    expectClean('1. 起草提纲\n2. 收集资料\n3. 完成初稿')
  })

  it('nested mixed list', () => {
    expectClean('- 外层一\n  - 内层 a\n  - 内层 b\n- 外层二\n  1. 步骤一\n  2. 步骤二')
  })

  it('task list', () => {
    expectClean('- [ ] 未完成的任务\n- [x] 已完成的任务\n- [ ] 还有一个')
  })

  it('nested task list', () => {
    expectClean('- [ ] 主任务\n  - [x] 子任务甲\n  - [ ] 子任务乙')
  })

  it('table', () => {
    expectClean(
      '| 方案 | 优点 | 缺点 |\n| --- | --- | --- |\n| offset 分页 | 实现简单 | 深分页慢 |\n| cursor 分页 | 性能稳定 | 无法跳页 |'
    )
  })

  it('table with alignment markers', () => {
    expectClean('| 左对齐 | 居中 | 右对齐 |\n| :--- | :---: | ---: |\n| a | b | c |')
  })

  it('blockquote', () => {
    expectClean('> 引用的一段话，关于文档即状态。')
  })

  it('nested blockquote', () => {
    expectClean('> 外层引用\n> > 内层引用（嵌套）')
  })

  it('horizontal rule', () => {
    expectClean('上文\n\n---\n\n下文')
  })

  it('link', () => {
    expectClean('见 [产品 spec](https://example.com/spec.md) 第 2 节。')
  })

  it('image with workspace-relative path', () => {
    expectClean('![架构图](uploads/arch.png)')
  })

  it('image with absolute URL and title', () => {
    expectClean('![logo](https://example.com/logo.png "站点 logo")')
  })

  it('cheese tokens <@> <#> <&> survive as literal text', () => {
    expectClean('请 <@mentor-1> 看下 <#0f14e0ab-1234-5678-9abc-def012345678> 里的 <&docs/plan.md>。')
  })

  it('consecutive blank lines collapse without losing content', () => {
    expectClean('第一段\n\n\n\n第二段\n\n\n第三段')
  })

  // CommonMark says a closing `**` preceded by punctuation and followed by a
  // letter cannot close. Chinese puts no space after the delimiter, so this is
  // how bold is normally written here — it has to parse, not survive as
  // literal asterisks. (See docMarkdown.ts's CJK-friendly note.)
  it('bold closing before a CJK letter', () => {
    expectClean('按**执行档案（ExecutionProfile）**解析出模型。\n')
  })

  it('bold closing on a full stop, sentence continues', () => {
    expectClean('**这句话是粗体。**下一句不是。\n')
  })

  it('strikethrough closing before a CJK letter', () => {
    expectClean('前面~~删除（括号）~~后面\n')
  })

  // The same relaxation must not reach English, where the rule is doing its job.
  it('ASCII emphasis is unaffected', () => {
    expectClean('a **bold (paren)** b, an *italic* and a ~~strike~~.\n')
  })

  it('CJK/English mixed prose', () => {
    expectClean(
      '这是一段中英混排 mixed-language paragraph，包含 100% 的数字、英文 words 和标点：句号。逗号，分号；括号（成对）。'
    )
  })

  it('soft-wrapped paragraph lines', () => {
    expectClean('第一行接着\n第二行（软换行，同一段落）')
  })

  // The serializer escapes these on sight; neither can open anything alone, and
  // the backslash would otherwise be written into the file on the next save.
  it('a lone tilde in prose', () => {
    expectClean('未碰 P1~P4 的条目。\n')
  })

  // Two of them on one line ARE a GFM strikethrough pair, and the round trip
  // must keep saying so — the escape is what stops them pairing, and dropping
  // it where it matters would rewrite the document.
  it('a tilde pair still reads as strikethrough', () => {
    editor.commands.setContent('未碰 P1~P4 和 P5~P8 的条目。\n', { contentType: 'markdown' })
    expect(JSON.stringify(editor.getJSON())).toContain('strike')
  })

  it('square brackets that are not a link', () => {
    expectClean('cheesex-app[bot] 合并了 arr[0] 和 arr[1]。\n')
  })

  it('brackets that ARE a link stay a link', () => {
    expectClean('看 [这里](https://example.com) 和 a[b]c。\n')
  })

  it('bare < and & characters in prose', () => {
    expectClean('数学上 a < b 且 AT&T 是公司名。')
  })

  it('bare URL (autolink)', () => {
    expectClean('直接贴地址 https://example.com/page 这样。')
  })

  it('full composite document', () => {
    expectClean(
      [
        '## 背景',
        '',
        '这是**综合**文档，含 `code` 与 [链接](https://example.com)。',
        '',
        '- [ ] 待办一',
        '- [x] 待办二',
        '',
        '| A | B |',
        '| --- | --- |',
        '| 1 | 2 |',
        '',
        '> 引用收尾',
        '',
        '---',
        '',
        '```js',
        'console.log("done")',
        '```',
      ].join('\n')
    )
  })
})

// Syntax the editor is KNOWN to renormalize/lose — these must be caught by
// the lossy detector (banner + autosave pause), which is the 军规 1 backstop.
describe('known-lossy constructs are detected', () => {
  function expectDetected(md: string) {
    const report = compareRoundTrip(md, roundTrip(md))
    expect(report.clean).toBe(false)
  }

  it('setext headings get rewritten to ATX', () => {
    expectDetected('标题\n===\n\n正文')
  })

  it('reference-style link definitions are inlined (def line dropped)', () => {
    expectDetected('看[这里][1]。\n\n[1]: https://example.com')
  })
})

describe('rule 11: intraword underscores', () => {
  it('bare identifiers in prose do not flag lossy', () => {
    const md = '索引只在 project_id 等外键上，补 (project_id, created_at, id)。\n'
    expect(compareRoundTrip(md, roundTrip(md)).clean).toBe(true)
  })
  it('inline-code identifiers stay strict', () => {
    const md = '用 `project_id` 查询。\n'
    expect(compareRoundTrip(md, roundTrip(md)).clean).toBe(true)
  })

  // A table cell is prose. Our documents are mostly tables of identifiers, so
  // skipping this rule there meant almost every one of them read as unsafe.
  it('applies inside table cells', () => {
    const md = '| 结论 | 依据 |\n| --- | --- |\n| 属实 | login_security.py 里 |\n'
    expect(compareRoundTrip(md, roundTrip(md)).clean).toBe(true)
  })

  it('applies inside blockquotes', () => {
    const md = '> 索引建在 project_id 上。\n'
    expect(compareRoundTrip(md, roundTrip(md)).clean).toBe(true)
  })
})
