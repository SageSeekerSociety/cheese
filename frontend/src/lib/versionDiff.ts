// 文档两版之间逐行（一段一行）看改了什么，给「修改记录」的对比用。
//
// 和 paragraphDiff 的区别：那边吃的是后端给的统一格式 diff、只列改了的段；这里手上
// 已经有两版全文（历史接口每版都带全文），在前端直接比，并且保留没改的上下文——人要
// 看出改的那一处在文章的哪儿。没改的长段落折成一行「N 段未改」。
//
// 相邻的删和加配成一对「改了的一段」，行内再用 inlineDiff 标出改了哪几个字。
import type { Segment } from './paragraphDiff'

import { inlineDiff } from './paragraphDiff'

export type DiffRow =
  | { kind: 'same'; text: string }
  | { kind: 'add'; text: string }
  | { kind: 'del'; text: string }
  | { kind: 'changed'; segments: Segment[] }
  | { kind: 'skip'; count: number }

export interface VersionDiff {
  rows: DiffRow[]
  added: number
  removed: number
  changed: number
}

/** 改动前后各留几段没改的上下文。 */
const CONTEXT = 2
// 行数相乘超过这个量就不再逐行找最长公共子序列：整篇算删了又加了，比卡住页面好。
const MAX_CELLS = 4_000_000

type Op = { kind: 'same' | 'add' | 'del'; text: string }

function lineOps(a: string[], b: string[]): Op[] {
  // 先去掉首尾相同的部分：改一处的文档，大部分行都在这两截里。
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start++
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--
    endB--
  }
  const head: Op[] = a.slice(0, start).map((text) => ({ kind: 'same', text }))
  const tail: Op[] = a.slice(endA).map((text) => ({ kind: 'same', text }))
  const midA = a.slice(start, endA)
  const midB = b.slice(start, endB)
  const mid: Op[] = []
  if (midA.length * midB.length > MAX_CELLS) {
    for (const text of midA) mid.push({ kind: 'del', text })
    for (const text of midB) mid.push({ kind: 'add', text })
    return [...head, ...mid, ...tail]
  }
  const width = midB.length + 1
  const lcs = new Uint32Array((midA.length + 1) * width)
  for (let i = midA.length - 1; i >= 0; i--) {
    for (let j = midB.length - 1; j >= 0; j--) {
      lcs[i * width + j] =
        midA[i] === midB[j]
          ? lcs[(i + 1) * width + j + 1] + 1
          : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1])
    }
  }
  let i = 0
  let j = 0
  while (i < midA.length && j < midB.length) {
    if (midA[i] === midB[j]) {
      mid.push({ kind: 'same', text: midA[i++] })
      j++
    } else if (lcs[(i + 1) * width + j] >= lcs[i * width + j + 1]) {
      mid.push({ kind: 'del', text: midA[i++] })
    } else {
      mid.push({ kind: 'add', text: midB[j++] })
    }
  }
  while (i < midA.length) mid.push({ kind: 'del', text: midA[i++] })
  while (j < midB.length) mid.push({ kind: 'add', text: midB[j++] })
  return [...head, ...mid, ...tail]
}

/** 相邻的一串删、一串加：一对一配成「改了的一段」，多出来的照旧是删或加。 */
function pairChanges(ops: Op[]): DiffRow[] {
  const out: DiffRow[] = []
  let k = 0
  while (k < ops.length) {
    if (ops[k].kind === 'same') {
      out.push({ kind: 'same', text: ops[k++].text })
      continue
    }
    const dels: string[] = []
    const adds: string[] = []
    while (k < ops.length && ops[k].kind !== 'same') {
      if (ops[k].kind === 'del') dels.push(ops[k].text)
      else adds.push(ops[k].text)
      k++
    }
    const pairs = Math.min(dels.length, adds.length)
    for (let p = 0; p < pairs; p++) out.push({ kind: 'changed', segments: inlineDiff(dels[p], adds[p]) })
    for (const text of dels.slice(pairs)) out.push({ kind: 'del', text })
    for (const text of adds.slice(pairs)) out.push({ kind: 'add', text })
  }
  return out
}

/** 没改的长段折起来，只在改动前后各留 CONTEXT 段。 */
function collapse(rows: DiffRow[]): DiffRow[] {
  const keep = rows.map((row) => row.kind !== 'same')
  rows.forEach((row, index) => {
    if (row.kind === 'same') return
    for (let d = 1; d <= CONTEXT; d++) {
      if (index - d >= 0) keep[index - d] = true
      if (index + d < rows.length) keep[index + d] = true
    }
  })
  const out: DiffRow[] = []
  let skipped = 0
  rows.forEach((row, index) => {
    if (keep[index]) {
      if (skipped) out.push({ kind: 'skip', count: skipped })
      skipped = 0
      out.push(row)
    } else skipped++
  })
  if (skipped) out.push({ kind: 'skip', count: skipped })
  return out
}

/** `before` → `after` 改了什么。`before` 为 null 时（第一版）整篇算新加的。 */
export function versionDiff(before: string | null, after: string): VersionDiff {
  // 一段一行；段与段之间的空行不算一段（同 paragraphDiff），否则「加了一段」会数成两段，
  // 还多出一行空的绿底。
  const lines = (text: string) => text.split('\n').filter((line) => line.trim() !== '')
  const rows = pairChanges(lineOps(before === null ? [] : lines(before), lines(after)))
  return {
    rows: collapse(rows),
    added: rows.filter((r) => r.kind === 'add').length,
    removed: rows.filter((r) => r.kind === 'del').length,
    changed: rows.filter((r) => r.kind === 'changed').length,
  }
}
