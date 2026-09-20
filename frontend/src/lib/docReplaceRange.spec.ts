// @vitest-environment jsdom
// 文档从服务端刷新时，到底重写了多少。
//
// 这一份钉的是「不该动的段落不许动」：实况文档改一行，整格却往下跳一下，因为安装
// 新版本走的是整份替换，每一个段落的 DOM 都被重建。docReplaceRange 算出的区间是这
// 件事的度量衡——区间之外的节点必须原封不动，读的人才留在原地。
import { Editor } from '@tiptap/core'
import { TextSelection } from '@tiptap/pm/state'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { docExtensions, docReplaceRange } from './docMarkdown'

let editor: Editor

beforeAll(() => {
  editor = new Editor({ element: document.createElement('div'), extensions: docExtensions(), content: '' })
})

afterAll(() => {
  editor.destroy()
})

/** 一份 markdown 解析成的文档，走的是编辑器安装内容时的同一条解析路径。 */
function parse(md: string) {
  const manager = editor.markdown
  if (!manager) throw new Error('editor has no markdown manager')
  return editor.schema.nodeFromJSON(manager.parse(md))
}

/** 把区间之外的顶层节点收集出来——它们是「不许动」的那一批。 */
function nodesOutside(doc: ReturnType<typeof parse>, from: number, to: number) {
  const kept: string[] = []
  doc.forEach((node, offset) => {
    if (offset + node.nodeSize <= from || offset >= to) kept.push(node.textContent)
  })
  return kept
}

describe('docReplaceRange', () => {
  it('两版一模一样时什么都不用重写', () => {
    const md = '# 标题\n\n第一段\n\n第二段\n'
    expect(docReplaceRange(parse(md), parse(md))).toBeNull()
  })

  it('只改中间一段，区间就只盖住那一段，前后都留在原地', () => {
    const before = parse('# 标题\n\n第一段\n\n第二段\n\n第三段\n')
    const after = parse('# 标题\n\n第一段\n\n第二段改了\n\n第三段\n')
    const range = docReplaceRange(before, after)
    expect(range).not.toBeNull()

    const untouched = nodesOutside(before, range!.from, range!.to)
    expect(untouched, '标题和没改的两段都不该落进重写区间').toEqual(['标题', '第一段', '第三段'])
  })

  it('末尾追加一段时，前面的段落一个都不进区间', () => {
    const before = parse('第一段\n\n第二段\n')
    const after = parse('第一段\n\n第二段\n\n第三段\n')
    const range = docReplaceRange(before, after)
    expect(range).not.toBeNull()
    expect(nodesOutside(before, range!.from, range!.to)).toEqual(['第一段', '第二段'])
  })

  it('删掉中间一段时区间仍然合法，且不吃掉两头', () => {
    const before = parse('第一段\n\n第二段\n\n第三段\n')
    const after = parse('第一段\n\n第三段\n')
    const range = docReplaceRange(before, after)
    expect(range).not.toBeNull()
    expect(range!.to, 'to 不能跑到 from 前面去').toBeGreaterThanOrEqual(range!.from)
    expect(range!.sliceTo).toBeGreaterThanOrEqual(range!.from)
    expect(nodesOutside(before, range!.from, range!.to)).toContain('第一段')
  })

  it('算出来的区间装回去，得到的就是新版本', () => {
    const cases: [string, string][] = [
      ['# 标题\n\n第一段\n\n第二段\n', '# 标题\n\n第一段改了\n\n第二段\n'],
      ['第一段\n\n第二段\n', '第一段\n\n第二段\n\n第三段\n'],
      ['第一段\n\n第二段\n\n第三段\n', '第一段\n\n第三段\n'],
      ['只有一段\n', '# 换成标题\n\n还多了一段\n'],
      ['- 甲\n- 乙\n', '- 甲\n- 乙\n- 丙\n'],
      ['| a | b |\n| --- | --- |\n| 1 | 2 |\n', '| a | b |\n| --- | --- |\n| 1 | 3 |\n'],
    ]
    for (const [beforeMd, afterMd] of cases) {
      const before = parse(beforeMd)
      const after = parse(afterMd)
      const range = docReplaceRange(before, after)
      expect(range, `${beforeMd} → ${afterMd} 应该有差异`).not.toBeNull()
      const patched = before.replace(range!.from, range!.to, after.slice(range!.from, range!.sliceTo))
      expect(patched.eq(after), `${beforeMd} → ${afterMd} 补出来的文档必须等于新版本`).toBe(true)
    }
  })
})

describe('光标', () => {
  // 实况文档是可以点进去的（它是个编辑器）。把光标放进某一段只是「我在看这儿」，
  // 不会让文档变 dirty，所以芝士的更新照常装进来。此时整份替换会把光标一路映射到
  // 文末——人还在读上面，插入点却跑到了最后一行；编辑器有焦点时 ProseMirror 还要
  // 把它滚进视野。只替换差异区间就没有这回事：改动在光标下面，光标一动不动。
  function caretAt(ed: Editor, text: string): number {
    let pos = -1
    ed.state.doc.forEach((node, offset) => {
      if (node.textContent === text) pos = offset + 2
    })
    if (pos < 0) throw new Error(`找不到「${text}」`)
    ed.view.dispatch(ed.state.tr.setSelection(TextSelection.create(ed.state.doc, pos)))
    return pos
  }

  const BEFORE = '# 标题\n\n第一段\n\n第二段\n'
  const AFTER = '# 标题\n\n第一段\n\n第二段改了\n'

  it('改动落在光标下面时，光标留在原地', () => {
    const ed = new Editor({ element: document.createElement('div'), extensions: docExtensions(), content: '' })
    try {
      ed.commands.setContent(BEFORE, { contentType: 'markdown' })
      const at = caretAt(ed, '第一段')

      const next = ed.schema.nodeFromJSON(ed.markdown!.parse(AFTER))
      const range = docReplaceRange(ed.state.doc, next)
      expect(range).not.toBeNull()
      ed.view.dispatch(ed.state.tr.replace(range!.from, range!.to, next.slice(range!.from, range!.sliceTo)))

      expect(ed.state.selection.from, '人还在读上面那一段，插入点不该被弹走').toBe(at)
    } finally {
      ed.destroy()
    }
  })

  it('对照：整份替换会把光标甩到文末', () => {
    const ed = new Editor({ element: document.createElement('div'), extensions: docExtensions(), content: '' })
    try {
      ed.commands.setContent(BEFORE, { contentType: 'markdown' })
      const at = caretAt(ed, '第一段')
      ed.commands.setContent(AFTER, { contentType: 'markdown' })
      expect(ed.state.selection.from, '这就是上面那条测试防的事').not.toBe(at)
    } finally {
      ed.destroy()
    }
  })
})
