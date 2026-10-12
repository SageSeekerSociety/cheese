// 两个序列之间的最长公共子序列：逐行、逐词比对的唯一一份实现。
//
// 表是 (n+1)×(m+1) 的定长数组。两边长度相乘超过 LCS_MAX_CELLS 就不建表，整个 a 算
// 删了、整个 b 算加了：慢到卡住页面比少标几处相同更糟。

export type LcsOp<T> = { kind: 'same' | 'add' | 'del'; item: T }

export const LCS_MAX_CELLS = 4_000_000

/**
 * `a` → `b` 的逐项操作。同一项在两边都有就是 `same`，只在 a 有是 `del`，只在 b 有
 * 是 `add`。最长公共子序列不唯一时，`preferAdd` 为 false 先删后加，为 true 先加后删。
 */
export function lcsOps<T>(a: readonly T[], b: readonly T[], preferAdd = false): LcsOp<T>[] {
  const out: LcsOp<T>[] = []
  if (a.length * b.length > LCS_MAX_CELLS) {
    for (const item of a) out.push({ kind: 'del', item })
    for (const item of b) out.push({ kind: 'add', item })
    return out
  }
  // 从后往前填，这样可以从前往后读出结果。
  const width = b.length + 1
  const table = new Uint32Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * width + j] =
        a[i] === b[j]
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1])
    }
  }
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', item: a[i++] })
      j++
    } else if (
      preferAdd
        ? table[i * width + j + 1] >= table[(i + 1) * width + j]
        : table[(i + 1) * width + j] < table[i * width + j + 1]
    ) {
      out.push({ kind: 'add', item: b[j++] })
    } else {
      out.push({ kind: 'del', item: a[i++] })
    }
  }
  while (i < a.length) out.push({ kind: 'del', item: a[i++] })
  while (j < b.length) out.push({ kind: 'add', item: b[j++] })
  return out
}
