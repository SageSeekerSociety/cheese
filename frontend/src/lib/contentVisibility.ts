// 屏幕外的内容不参与 layout / paint，但必须留在 DOM 与可访问性树里——Ctrl+F、读屏、
// 选中都要找得到它，这也正是「不虚拟化」的意思。`content-visibility: auto` 就是这个
// 语义：跳过离屏内容的渲染，`contain-intrinsic-size: auto <h>` 先拿估计高度占位、渲染
// 过一次之后记住真实高度（样式在 room-row.css / PanelSite.vue / style.css）。行数多
// 的时间线（对话栏、现场）、长文档的正文块都靠它省下离屏那部分的渲染。
//
// 估计值会咬到两处「必须按真实高度算」的地方：
//   1. 向上翻页按 scrollHeight 的差补偿 scrollTop（lib/blockPaging 的
//      scrollTopAfterPrepend）——量到的是估计高度，补进去的就是错的，翻页会跳。
//   2. 长回复折起来/现场某条要不要夹的判定（lib/chatFold、useSiteClamp）——同样量的是
//      被夹那一层的高度。
// 两处测量前挂上 MEASURE_CLASS：它关掉子树里的 content-visibility，让离屏内容也按真实
// 高度铺开，量完再撤——撤掉之后已经渲染过的行被 `auto` 记住，高度不再变，跳不回去。
//
// 代价是每次测量要把这一窗都铺开一帧——这正是加 content-visibility 之前一直以来的
// 样子，且只在翻页/折叠判定那一下，不是常态滚动。

/** 测量帧挂在这一窗的滚动容器上：这一帧里子树关掉 content-visibility。 */
export const MEASURE_CLASS = 'cv-measure'

/**
 * 进入「按真实高度量」的一帧：给滚动容器挂上 MEASURE_CLASS。之后读到的那一次 layout
 * 会把离屏内容也铺开——读取（scrollHeight 等）强制布局，这正是我们要的。
 */
export function beginMeasuredLayout(scroller: HTMLElement | null): void {
  scroller?.classList.add(MEASURE_CLASS)
}

/**
 * 量完撤掉。让这一帧先按真实高度画一次再撤：`contain-intrinsic-size: auto` 的「记住的
 * 高度」要在内容被渲染过之后才记得下，紧接着撤的话，行在还原之前又被跳过，量到的真实
 * 高度就白量了。两个 rAF —— 第一个 rAF 的那一帧仍带着类、会被布局+绘制，第二个 rAF
 * 落在那次绘制之后，这时再撤。
 */
export function endMeasuredLayout(scroller: HTMLElement | null): void {
  if (!scroller) return
  requestAnimationFrame(() => requestAnimationFrame(() => scroller.classList.remove(MEASURE_CLASS)))
}
