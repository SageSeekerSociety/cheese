// 一条很长的队友回复收成一段高度，「展开 / 收起」切换。
//
// 收起的高度是一段**阅读行数**，不是字数：同一段回复在宽栏里是十二行，在窄栏里
// 是三十行，按字数判会把其中一个折了、另一个漏了。行数换成像素时要乘这条正文自己的
// 行高（`--lh-15-reading`，24px），所以真正的高度由调用处的 `getComputedStyle` 量出来，
// 这个模块只留**判据**：渲染出来的正文是不是高过了这段预算。
//
// 量的是渲染后的元素（`scrollHeight`），不是原文：代码块、表格、图片的高度都不是
// 从正文字数能推出来的。

/** 收起时保留的阅读行数。 */
export const REPLY_FOLD_LINES = 20

/** 行高 `lineHeight`（px）的正文，收起后的最大高度（px）。 */
export function foldHeight(lineHeight: number, lines: number = REPLY_FOLD_LINES): number {
  const perLine = Number.isFinite(lineHeight) && lineHeight > 0 ? lineHeight : 0
  return Math.round(perLine * lines)
}

/**
 * 渲染出来的正文（`contentHeight`，px）是不是高过这段预算（`maxHeight`，px）。
 *
 * 多出的那 1px 是给行高不是整数的情形留的余量：`scrollHeight` 四舍五入到整数，
 * 一行正好卡在预算上时不能因为它多算了零点几像素就折起来。
 */
export function overflowsFold(contentHeight: number, maxHeight: number): boolean {
  return maxHeight > 0 && contentHeight > maxHeight + 1
}
