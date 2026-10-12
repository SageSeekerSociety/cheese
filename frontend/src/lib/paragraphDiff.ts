// 文档两版之间按「段」看改了什么。
//
// 两份文字先按行比成一份统一格式的 diff（`compareTexts`）。按行读它对代码是对的，
// 对文档不是：一段改了两个字，行 diff 只会说「删掉一整段、加上一整段」，人得自己
// 对着两段找不同。这里把相邻的删和加配成一对「改了的一段」，再在这一对里标出到底
// 改了哪几个字。

import { lcsOps } from './lcs'

export type Segment = { text: string; kind: 'same' | 'add' | 'del' }

export type Paragraph =
  | { kind: 'changed'; at: number; segments: Segment[] }
  | { kind: 'added'; at: number; text: string }
  | { kind: 'removed'; at: number; text: string }

export interface ParagraphDiff {
  paragraphs: Paragraph[]
  changed: number
  added: number
  removed: number
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/** 统一格式的 diff → 改了的每一段。没改的段不列：它们不是人来这里要看的东西。 */
export function paragraphDiff(diff: string): ParagraphDiff {
  const paragraphs: Paragraph[] = []
  let dels: { at: number; text: string }[] = []
  let adds: { at: number; text: string }[] = []
  let oldAt = 0
  let newAt = 0

  function flush() {
    const pairs = Math.min(dels.length, adds.length)
    for (let i = 0; i < pairs; i++) {
      paragraphs.push({ kind: 'changed', at: adds[i].at, segments: inlineDiff(dels[i].text, adds[i].text) })
    }
    for (const del of dels.slice(pairs)) paragraphs.push({ kind: 'removed', at: del.at, text: del.text })
    for (const add of adds.slice(pairs)) paragraphs.push({ kind: 'added', at: add.at, text: add.text })
    dels = []
    adds = []
  }

  for (const line of diff.split('\n')) {
    const hunk = line.match(HUNK)
    if (hunk) {
      flush()
      oldAt = Number(hunk[1])
      newAt = Number(hunk[2])
    } else if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('\\')) {
      continue
    } else if (line.startsWith('-')) {
      dels.push({ at: oldAt++, text: line.slice(1) })
    } else if (line.startsWith('+')) {
      adds.push({ at: newAt++, text: line.slice(1) })
    } else if (line.startsWith(' ')) {
      flush()
      oldAt++
      newAt++
    }
  }
  flush()
  // 空行删了加了不算一段：正文里的空行只是段与段之间的间隔。
  const real = paragraphs.filter((p) => (p.kind === 'changed' ? p.segments.some((s) => s.text.trim()) : p.text.trim()))
  return {
    paragraphs: real,
    changed: real.filter((p) => p.kind === 'changed').length,
    added: real.filter((p) => p.kind === 'added').length,
    removed: real.filter((p) => p.kind === 'removed').length,
  }
}

// 英文和数字按词，中文按字：一个汉字就是一个词，按词切中文切不开。
const TOKEN = /[A-Za-z0-9_]+|\s+|./gsu

/** 同一段的两版里，哪些字没变、哪些删了、哪些加了。 */
export function inlineDiff(before: string, after: string): Segment[] {
  const out: Segment[] = []
  for (const { kind, item } of lcsOps(before.match(TOKEN) ?? [], after.match(TOKEN) ?? [])) {
    const last = out[out.length - 1]
    if (last?.kind === kind) last.text += item
    else out.push({ text: item, kind })
  }
  return out
}

/** 两版正文（一段一行）之间改了的每一段：先按行求最长公共子序列，拼成一份统一格式
 *  的 diff，再交给 `paragraphDiff` 配对、细分。 */
export function compareTexts(before: string, after: string): ParagraphDiff {
  const a = before ? before.split('\n') : []
  const b = after ? after.split('\n') : []
  const marks = { same: ' ', add: '+', del: '-' }
  const lines = ['@@ -1 +1 @@', ...lcsOps(a, b, true).map(({ kind, item }) => `${marks[kind]}${item}`)]
  return paragraphDiff(lines.join('\n'))
}
