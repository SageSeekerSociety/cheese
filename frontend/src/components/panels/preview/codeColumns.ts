// 长行横着滚的时候，读者看不出自己在哪一段：一行三十万字节的压缩 JSON 展开是两百多万
// 像素，滚过一半和滚到开头在屏幕上长得一模一样。这里把「现在露出来的是第几列到第几列、
// 一共多少列」算出来，给一个能读的坐标。
//
// 列按等宽字符的数：一个字符一列，制表符按 `tabSize` 折算——那正是它在屏幕上的宽度，
// 读者数格子时数的也是这个。
//
// 行号那一列钉在左边、一直盖着正文，所以可见宽度要从它右边算起。

export interface VisibleColumns {
  /** 现在露出来的第一列（从 1 数）。 */
  start: number
  /** 现在露出来的最后一列（含）。 */
  end: number
  /** 最宽那一行有多少列。 */
  total: number
}

/** 一份等宽文本里「一行有多少列」：制表符按 `tabSize` 折算，其余一个字符一列。 */
export function columnsOf(line: string, tabSize: number): number {
  let width = 0
  for (const char of line) width += char === '\t' ? tabSize : 1
  return width
}

/**
 * 现在露出来的是哪几列。
 *
 * `charWidth` 是量出来的一个字符的像素宽：等宽字体里它就够了，不必去数 DOM。
 * 横滚到哪由 `scrollLeft` 说；露出多少由 `clientWidth` 减掉钉住的行号列。
 *
 * 没横滚、或者整行都露得出来时，起点就是第 1 列。
 */
export function visibleColumns(input: {
  scrollLeft: number
  viewWidth: number
  gutterWidth: number
  charWidth: number
  total: number
}): VisibleColumns {
  const total = Math.max(1, Math.round(input.total))
  const usable = Math.max(0, input.viewWidth - input.gutterWidth)
  if (input.charWidth <= 0 || usable <= 0) return { start: 1, end: total, total }
  // 行号列钉在左边，所以横滚量是从它右边那一点算起的。
  const first = Math.max(0, Math.round(input.scrollLeft / input.charWidth))
  const last = first + Math.max(1, Math.floor(usable / input.charWidth)) - 1
  return {
    start: Math.min(first + 1, total),
    end: Math.min(Math.max(last + 1, first + 1), total),
    total,
  }
}
