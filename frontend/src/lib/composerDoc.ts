// 输入框里的正文和编辑器里的文档怎么互相换算。
//
// 输入框的正文始终是一段字符串，就是要发出去的那段（`请 <@zhangsan> 看一下`）：草稿、
// 「叫没叫芝士」、发送都只认它。编辑器里那份文档只是它的另一种写法——一行一个段落，
// 段落里只有文字——所以两边可以按字符一一对上，光标位置也能来回换算。
import type { Node as PMNode, Schema } from '@tiptap/pm/model'
import type { Transaction } from '@tiptap/pm/state'

import { Fragment, Slice } from '@tiptap/pm/model'

/** 文档读回成字符串：段落之间是换行。 */
export function docText(doc: PMNode): string {
  const lines: string[] = []
  doc.forEach((para) => lines.push(para.textContent))
  return lines.join('\n')
}

/** 字符串写成编辑器的初始内容（TipTap 的 JSON 写法）。 */
export function textContent(text: string) {
  return {
    type: 'doc',
    content: text
      .split('\n')
      .map((line) => (line ? { type: 'paragraph', content: [{ type: 'text', text: line }] } : { type: 'paragraph' })),
  }
}

/** 正文里第 `offset` 个字符前面那个位置，在文档里是第几格。 */
export function posAt(doc: PMNode, offset: number): number {
  let rest = Math.max(0, offset)
  let pos = 0
  for (let i = 0; i < doc.childCount; i++) {
    const para = doc.child(i)
    if (rest <= para.content.size) return pos + 1 + rest
    rest -= para.content.size + 1
    pos += para.nodeSize
  }
  return Math.max(1, doc.content.size - 1)
}

/** 文档里的第 `pos` 格，落在正文的第几个字符前面。 */
export function offsetAt(doc: PMNode, pos: number): number {
  let offset = 0
  let start = 0
  for (let i = 0; i < doc.childCount; i++) {
    const para = doc.child(i)
    const end = start + para.nodeSize
    if (pos < end) return offset + Math.min(Math.max(0, pos - start - 1), para.content.size)
    offset += para.content.size + 1
    start = end
  }
  return Math.max(0, offset - 1)
}

/** 把正文的 [from, to) 换成 `text`；`text` 里的换行变成新的段落。 */
export function replaceText(tr: Transaction, schema: Schema, from: number, to: number, text: string): Transaction {
  const a = posAt(tr.doc, from)
  const b = posAt(tr.doc, to)
  if (!text.includes('\n')) return text ? tr.insertText(text, a, b) : tr.delete(a, b)
  const paras = text.split('\n').map((line) => schema.nodes.paragraph.create(null, line ? schema.text(line) : null))
  return tr.replace(a, b, new Slice(Fragment.from(paras), 1, 1))
}

/** 从 `before` 改成 `after`，最小的那一处改动：前后相同的部分不动。 */
export function textChange(before: string, after: string): { from: number; to: number; text: string } {
  let start = 0
  const max = Math.min(before.length, after.length)
  while (start < max && before[start] === after[start]) start++
  let tail = 0
  while (tail < max - start && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail++
  return { from: start, to: before.length - tail, text: after.slice(start, after.length - tail) }
}
