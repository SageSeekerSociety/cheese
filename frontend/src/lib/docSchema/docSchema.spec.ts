// @vitest-environment jsdom
// Living-doc round-trip corpus (军规 1: never silently drop content).
//
// Every case asserts: parse(markdown) → serialize ≡ original under the
// documented normalization rules (see fidelity.ts). A failing case here is
// syntax converting a Markdown document into the live document would corrupt —
// fix the editor config, or make sure compareRoundTrip reports it.
import type { JSONContent } from '@tiptap/core'

import { Editor } from '@tiptap/core'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { compareRoundTrip, docExtensions, normalizeMarkdown, serializeDoc } from '.'

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
  it('allows equivalent emphasis around links and escaped literal punctuation', () => {
    expectClean('**[官方资料](https://example.org)**')
    expectClean('**[作者。** _论文题名_](https://example.org)')
  })

  it('still rejects lost link destinations, emphasis and raw HTML', () => {
    expect(compareRoundTrip('[资料](https://example.org)', '[资料](https://different.org)').clean).toBe(false)
    expect(compareRoundTrip('**资料**', '资料').clean).toBe(false)
    expect(compareRoundTrip('<img src="diagram.png">', '').clean).toBe(false)
  })
  it('collapses blank-line runs and trailing whitespace', () => {
    expect(normalizeMarkdown('a  \n\n\n\nb\n')).toBe('a\n\nb')
  })
  it('normalizes table padding and separator dashes', () => {
    expect(normalizeMarkdown('| a  |  b |\n|----|:---:|')).toBe('| a | b |\n| --- | :---: |')
  })
  it('normalizes bullet markers to -', () => {
    expect(normalizeMarkdown('* one\n+ two')).toBe('- one\n- two')
  })
  it('puts a blank line on a block boundary written without one', () => {
    expect(normalizeMarkdown('正文\n## 小节\n下一段')).toBe(normalizeMarkdown('正文\n\n## 小节\n\n下一段'))
    expect(normalizeMarkdown('放哪个：\n- 一\n- 二')).toBe(normalizeMarkdown('放哪个：\n\n- 一\n- 二'))
  })

  it('ignores the indent a wrapped paragraph line was written at', () => {
    expect(normalizeMarkdown('1. 第一条\n    续行\n2. 第二条')).toBe(normalizeMarkdown('1. 第一条\n  续行\n2. 第二条'))
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
  it('preserves standalone HTML comments around editable prose', () => {
    const md = '# Plan\n\n<!-- BEGIN-PLAN -->\n\nObserve three crossings.\n\n<!-- END-PLAN -->'
    expectClean(md)
    editor.commands.insertContentAt(1, 'Updated ')
    expect(serializeDoc(editor)).toContain('# Updated Plan')
    expect(serializeDoc(editor)).toContain('<!-- BEGIN-PLAN -->')
    expect(serializeDoc(editor)).toContain('<!-- END-PLAN -->')
    expect(editor.getHTML()).toContain('hidden=""')
    expectClean('<!-- Multiple\nlines -->\n\nEditable prose.')
    expectClean('<!-- BEGIN-PLAN -->\nObserve three crossings.\n<!-- END-PLAN -->')
  })

  it('headings 1-4', () => {
    expectClean('# 一级标题\n\n## 二级 Heading\n\n### 三级\n\n#### 四级标题')
  })

  it('highlight', () => {
    expectClean('这一句里有 <mark>要先看的</mark> 几个字，**<mark>加粗的高亮</mark>**也一样。')
  })

  it('double equals in prose stay text', () => {
    expectClean('当 a == b 并且 c == d 时成立。')
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

  it('keeps the code of a fence indented by up to three spaces', () => {
    const rt = roundTrip('  ```python\n  # 第一行\n  # 第二行\n  ```')
    expect(rt).toContain('# 第一行\n# 第二行')
  })

  it('unordered list', () => {
    expectClean('- 第一项\n- 第二项 with English\n- 第三项')
  })

  it('ordered list', () => {
    expectClean('1. 起草提纲\n2. 收集资料\n3. 完成初稿')
  })

  it('reads the inline marks of a numbered item: bold, a status tag, and a line written on under it', () => {
    const md = '1. **每人一份面板** {✓ 建议先做}\n把卡汇到一处。\n2. **自动转入** {! 待定}\n有人提到。'
    const rt = roundTrip(md)
    expect(rt).not.toContain('\\*')
    expect(rt).toContain('1. **每人一份面板** {✓ 建议先做}')
    expect(rt).toContain('2. **自动转入** {! 待定}')
    const items = (editor.getJSON() as JSONContent).content![0].content!
    expect(items).toHaveLength(2)
    const first = items[0].content![0].content!
    expect(first[0]).toMatchObject({ text: '每人一份面板', marks: [{ type: 'bold' }] })
    expect(first[2]).toMatchObject({ text: '建议先做', marks: [{ type: 'status', attrs: { kind: 'ok' } }] })
    expect(first.map((n: JSONContent) => n.text).join('')).toContain('把卡汇到一处。')
  })

  it('keeps the start number and marks of a list that starts past one', () => {
    expect(roundTrip('3. 第三\n4. **第四**')).toBe('3. 第三\n4. **第四**')
  })

  it('nested mixed list', () => {
    expectClean('- 外层一\n  - 内层 a\n  - 内层 b\n- 外层二\n  1. 步骤一\n  2. 步骤二')
  })

  it('preserves sublists beneath numbered items, including two-digit markers', () => {
    for (const md of ['1. 来源一\n   - 方法说明\n\n2. 来源二', '10. 来源十\n    - 方法说明\n11. 来源十一']) {
      const rt = roundTrip(md)
      const firstTree = editor.getJSON()
      editor.commands.setContent(rt, { contentType: 'markdown' })
      expect(editor.getJSON()).toEqual(firstTree)
      expect(compareRoundTrip(md, rt).clean).toBe(true)
    }
  })

  it('keeps a code block under a numbered step, after a second paragraph too', () => {
    for (const md of [
      '1. 安装。\n   ```\n   curl x | sh\n   ```\n2. 下一步。',
      '1. 安装。\n\n   跑同一条命令：\n\n   ```\n   curl x | sh\n   ```\n\n2. 下一步。',
    ]) {
      const rt = roundTrip(md)
      expect(rt).toContain('\n   curl x | sh\n')
      expect(compareRoundTrip(md, rt).clean).toBe(true)
    }
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
  // literal asterisks. (See markdown.ts's CJK-friendly note.)
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

  // The parser reads any standard tag name as HTML and hands it to the schema,
  // which has no node for a lone one — so `<img>` used to take the words next
  // to it out of the document. It is text the author wrote, and stays text.
  it('a bare tag inside a sentence survives as literal text', () => {
    expectClean('前端优先渲染 <img>、@error 退回彩色首字母。')
  })

  it('a bare tag alone in its paragraph survives', () => {
    expectClean('<img>\n\n下一段')
    expectClean('前文\n\n<div>\n\n后文')
    expectClean('前文\n<img>\n后文')
  })

  it('an orphan closing tag survives', () => {
    expectClean('孤立 </p> 标签')
  })

  // `<br>` is a hard break and `<hr>` a thematic break — the schema holds both,
  // so a lone one keeps rendering as what it is.
  it('lone <br> and <hr> keep their meaning', () => {
    editor.commands.setContent('a <br> b', { contentType: 'markdown' })
    expect(editor.getHTML()).toContain('<br')
    editor.commands.setContent('上文\n\n---\n\n下文', { contentType: 'markdown' })
    expect(editor.getHTML()).toContain('<hr')
  })

  it('real elements still parse as elements', () => {
    editor.commands.setContent('<a href="https://example.com">见</a>', { contentType: 'markdown' })
    expect(editor.getHTML()).toContain('href="https://example.com"')
    editor.commands.setContent('<b>粗</b>', { contentType: 'markdown' })
    expect(editor.getHTML()).toContain('<strong>')
  })

  // GFM runs an autolink to the next whitespace and Chinese has none, so the
  // rest of the sentence used to become part of the URL — href included.
  it('an autolink stops before the Chinese that follows it', () => {
    editor.commands.setContent('见 http://host/status，通了(200，22ms)就说明后端健康', { contentType: 'markdown' })
    expect(editor.getHTML()).toContain('href="http://host/status"')
    expectClean('见 http://host/status，通了(200，22ms)就说明后端健康')
  })

  it('autolink boundaries: fullwidth punctuation, trailing comma, balanced parens', () => {
    expectClean('（http://x.example/a）括号')
    expectClean('见 https://a.example/x，后面。')
    editor.commands.setContent('见 https://a.example/x(b)，后面', { contentType: 'markdown' })
    expect(editor.getHTML()).toContain('href="https://a.example/x(b)"')
  })

  it('plain and www autolinks are unaffected', () => {
    expectClean('直接贴地址 https://example.com/page 这样。')
    expectClean('www.example.com中文')
  })

  // The visible text of an email autolink carries no `mailto:` prefix, so the
  // serializer used to write the link syntax into the file on every save.
  it('an email address is written back bare', () => {
    expectClean('manual-approval@v1.13.1 这个版本')
    expectClean('见 a@b.com 结束')
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

// Syntax the editor is KNOWN to renormalize/lose — these must be reported by
// the round-trip comparison, which is what says a conversion changed a document.
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

  // Rules 12/13 loosen whitespace, which is the one place a tolerance can go
  // too far: if it ever equated "two blocks" with "one block", the check would
  // stop seeing the loss it exists for. These pin the floor.
  it('two paragraphs merged into one is still lossy', () => {
    const merged = normalizeMarkdown('第一段。\n\n第二段。')
    expect(merged).not.toBe(normalizeMarkdown('第一段。 第二段。'))
  })

  it('a dropped blank line between paragraphs is still lossy', () => {
    expect(normalizeMarkdown('第一段。\n\n第二段。')).not.toBe(normalizeMarkdown('第一段。\n第二段。'))
  })

  it('a flattened nested list is still lossy', () => {
    expect(normalizeMarkdown('- 一级\n  - 二级')).not.toBe(normalizeMarkdown('- 一级\n- 二级'))
  })

  it('an indented code block keeps its indentation', () => {
    expect(normalizeMarkdown('正文\n\n    code()\n')).toContain('    code()')
  })

  it('keeps blank lines inside indented code that resembles a list', () => {
    expect(compareRoundTrip('    - first\n\n    - second', '    - first\n    - second').clean).toBe(false)
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
