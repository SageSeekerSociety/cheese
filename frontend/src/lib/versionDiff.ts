// 文档两版之间逐行（一段一行）看改了什么，给「修改记录」的对比用。
//
// 手上已经有两版全文（历史接口每版都带全文），在前端直接比，并且保留没改的上下文——
// 人要看出改的那一处在文章的哪儿。没改的长段落折成一行「N 段未改」。
//
// 相邻的删和加配成一对「改了的一段」，行内再用 inlineDiff 标出改了哪几个字。
import type { Segment } from './paragraphDiff'

import { lcsOps } from './lcs'
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
  const mid: Op[] = lcsOps(a.slice(start, endA), b.slice(start, endB)).map(({ kind, item }) => ({ kind, text: item }))
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

/** `before` → `after` 改了什么。`before` 为 null 时（第一版）整篇算新加的。
 *
 *  `collapse: false` 给整篇（没改的段原样留着）：预览里的对比是在整份文档上标出增删，
 *  读者要的是「在原文里看哪里变了」，不是一张改动清单。 */
export function versionDiff(before: string | null, after: string, opts: { collapse?: boolean } = {}): VersionDiff {
  // 一段一行；段与段之间的空行不算一段，否则「加了一段」会数成两段，
  // 还多出一行空的绿底。
  const lines = (text: string) => text.split('\n').filter((line) => line.trim() !== '')
  const rows = pairChanges(lineOps(before === null ? [] : lines(before), lines(after)))
  return {
    rows: opts.collapse === false ? rows : collapse(rows),
    added: rows.filter((r) => r.kind === 'add').length,
    removed: rows.filter((r) => r.kind === 'del').length,
    changed: rows.filter((r) => r.kind === 'changed').length,
  }
}

/** Markdown 里空行是结构（段落、列表、表格靠它分开），逐段比时不能丢：先换成一个看不见
 *  的占位字符，比完画的时候再换回空行（`components/panels/preview/PreviewVersionDiff.vue`）。 */
export const BLANK_LINE = '\u2063'

/** 「改了几处」：连在一起的增删算一处（重写了相邻两段是一次修改），只动了空行的不算。 */
export function changeRuns(rows: readonly DiffRow[]): number {
  let runs = 0
  let inRun = false
  for (const row of rows) {
    const changed = row.kind !== 'same' && row.kind !== 'skip' && !('text' in row && row.text === BLANK_LINE)
    if (changed && !inRun) runs += 1
    inRun = changed || (inRun && row.kind !== 'same' && row.kind !== 'skip')
  }
  return runs
}
