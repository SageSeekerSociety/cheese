// 两段脚本逐行比：哪几行是新加的、哪几行删掉了，其余照旧。脚本只有几十行，
// 最长公共子序列直接算，不必引一个库。

export interface DiffLine {
  kind: 'same' | 'added' | 'removed'
  text: string
}

export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split('\n')
  const b = after.split('\n')
  const keep: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      keep[i][j] = a[i] === b[j] ? keep[i + 1][j + 1] + 1 : Math.max(keep[i + 1][j], keep[i][j + 1])
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] })
      i++
      j++
    } else if (keep[i + 1][j] >= keep[i][j + 1]) out.push({ kind: 'removed', text: a[i++] })
    else out.push({ kind: 'added', text: b[j++] })
  }
  while (i < a.length) out.push({ kind: 'removed', text: a[i++] })
  while (j < b.length) out.push({ kind: 'added', text: b[j++] })
  return out
}
