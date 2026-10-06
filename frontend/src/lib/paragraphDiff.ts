// 文档两版之间按「段」看改了什么。
//
// 后端比文档时比的是正文，一段一行（documents/text.py 的 delivered_comparison），给回
// 来的是一份统一格式的 diff。按行读它对代码是对的，对文档不是：一段改了两个字，行
// diff 只会说「删掉一整段、加上一整段」，人得自己对着两段找不同。这里把相邻的删和加
// 配成一对「改了的一段」，再在这一对里标出到底改了哪几个字。

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

// 两段都很长时逐字比要 n×m 的表。超过这个量就不细分，整段算删了又加了：慢到卡住页
// 面比少标几个字更糟。
const MAX_CELLS = 4_000_000

/** 同一段的两版里，哪些字没变、哪些删了、哪些加了。 */
export function inlineDiff(before: string, after: string): Segment[] {
  const a = before.match(TOKEN) ?? []
  const b = after.match(TOKEN) ?? []
  if (a.length * b.length > MAX_CELLS) {
    return [
      { text: before, kind: 'del' as const },
      { text: after, kind: 'add' as const },
    ].filter((s) => s.text)
  }
  // 最长公共子序列，从后往前填，这样可以从前往后读出结果。
  const width = b.length + 1
  const lcs = new Uint32Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i * width + j] =
        a[i] === b[j] ? lcs[(i + 1) * width + j + 1] + 1 : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1])
    }
  }
  const out: Segment[] = []
  const push = (text: string, kind: Segment['kind']) => {
    const last = out[out.length - 1]
    if (last?.kind === kind) last.text += text
    else out.push({ text, kind })
  }
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push(a[i++], 'same')
      j++
    } else if (lcs[(i + 1) * width + j] >= lcs[i * width + j + 1]) {
      push(a[i++], 'del')
    } else {
      push(b[j++], 'add')
    }
  }
  while (i < a.length) push(a[i++], 'del')
  while (j < b.length) push(b[j++], 'add')
  return out
}

// 两份正文直接比。行数太多就不逐段配对，整篇算删了又加了，理由同 MAX_CELLS。
const MAX_LINE_CELLS = 4_000_000

/** 两版正文（一段一行）之间改了的每一段：先按行求最长公共子序列，拼成一份统一格式
 *  的 diff，再交给 `paragraphDiff` 配对、细分。 */
export function compareTexts(before: string, after: string): ParagraphDiff {
  const a = before ? before.split('\n') : []
  const b = after ? after.split('\n') : []
  const lines: string[] = ['@@ -1 +1 @@']
  if (a.length * b.length > MAX_LINE_CELLS) {
    lines.push(...a.map((line) => `-${line}`), ...b.map((line) => `+${line}`))
    return paragraphDiff(lines.join('\n'))
  }
  const width = b.length + 1
  const lcs = new Uint32Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i * width + j] =
        a[i] === b[j] ? lcs[(i + 1) * width + j + 1] + 1 : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1])
    }
  }
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      lines.push(` ${a[i]}`)
      i++
      j++
    } else if (j < b.length && (i >= a.length || lcs[i * width + j + 1] >= lcs[(i + 1) * width + j])) {
      lines.push(`+${b[j]}`)
      j++
    } else {
      lines.push(`-${a[i]}`)
      i++
    }
  }
  return paragraphDiff(lines.join('\n'))
}
