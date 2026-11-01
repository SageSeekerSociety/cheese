// 定位框弹在哪里：指的那一处在面板里的位置。
//
// 坐标是面板「内容」坐标（已经加上面板自己的 scrollTop），因为框绝对定位在面板里、跟着
// 内容一起滚。viewTop / viewBottom 是此刻可见的那一段，框要翻到上面还是放在下面看它。

export interface LocatorAnchor {
  top: number
  bottom: number
  left: number
  panelWidth: number
  viewTop: number
  viewBottom: number
}

/** 面板窄到放不下一张弹出的卡片时就贴底：这么窄的时候弹出来也只是把正文整个盖住。
 *
 *  这个数是「卡片真放得下」的那条线，不是「宽不宽」的感觉：卡片最宽 348、左右各留
 *  12，一共 372。原来定在 440，比卡片实际需要的宽出一截，结果手机（390）上明明放得下，
 *  卡片却被推到底边——读者刚选中的那一行在上面，卡片跑到屏幕另一头，中间隔着一屏正文，
 *  指认和框对不上。 */
export const FLOATING_MIN_WIDTH = 372

interface PointerMark {
  x: number
  y: number
  scroller: Element | null
  scrollTop: number
  /** 记下时面板自己滚到哪：面板一滚，内容（连同那一点）跟着挪，点也要跟着挪。 */
  panelScrollTop: number
}

/** 往上找第一个真在滚的祖先：表格、分页文档的舞台各自有一层滚动。 */
export function scrollParent(node: EventTarget | null, stop: Element): Element | null {
  let el = node instanceof Element ? node : null
  while (el && el !== stop) {
    const style = el.ownerDocument.defaultView?.getComputedStyle(el)
    if (style && /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight) return el
    el = el.parentElement
  }
  return null
}

export function markPointer(event: PointerEvent, panel: Element): PointerMark {
  const scroller = scrollParent(event.target, panel)
  return {
    x: event.clientX,
    y: event.clientY,
    scroller,
    scrollTop: scroller?.scrollTop ?? 0,
    panelScrollTop: panel.scrollTop,
  }
}

/** 选中的那一段还在面板里就记下它（克隆一份：页面上的选中会被下一次点击清掉）。 */
export function selectionIn(panel: Element): Range | null {
  const sel = panel.ownerDocument.getSelection()
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null
  const range = sel.getRangeAt(0)
  return panel.contains(range.commonAncestorContainer) ? range.cloneRange() : null
}

/**
 * 这一段选区实实在在占的那一块：把它的每一条矩形并起来，**零宽零高的不要**。
 *
 * 为什么不直接 `range.getBoundingClientRect()`：选区的那一头落在元素上时（拖过一整行、
 * 往上多拖一点，浏览器给回来的那一头常常是文字层的容器），它会带上浏览器补出来的那种
 * 零宽的整行块，量出来比屏幕上亮着的那一道高出一截——框于是落到选区下面很远，读者看到
 * 的就是「高亮在这儿、按钮在那儿」（2026-11-01 负责人报的第 2 条）。并真矩形就跟着亮的
 * 那一道走；一条真矩形都没有（空行上点了一下）时退回它给的整块，别把框丢了。
 */
function unionOfRects(range: Range): { top: number; bottom: number; left: number } | null {
  let top = Infinity
  let bottom = -Infinity
  let left = Infinity
  for (const rect of range.getClientRects()) {
    if (!rect.width || !rect.height) continue
    top = Math.min(top, rect.top)
    bottom = Math.max(bottom, rect.bottom)
    left = Math.min(left, rect.left)
  }
  if (top <= bottom) return { top, bottom, left }
  const all = range.getBoundingClientRect()
  return all.width || all.height ? { top: all.top, bottom: all.bottom, left: all.left } : null
}

/**
 * 按此刻的布局算一次。选中的一段每次现读它的矩形（内层滚动、换行都跟得上）；只有一个
 * 点的（点一格、钉一处）按记下以后内层和面板各滚了多少来平移。那一处不在可见区里就返回
 * null，框退回贴底。
 */
export function measureAnchor(
  panel: HTMLElement,
  range: Range | null,
  pointer: PointerMark | null
): LocatorAnchor | null {
  if (panel.clientWidth < FLOATING_MIN_WIDTH) return null
  const box = panel.getBoundingClientRect()
  let rect: { top: number; bottom: number; left: number } | null = null
  if (range) rect = unionOfRects(range)
  if (!rect && pointer) {
    const inner = pointer.scroller ? pointer.scroller.scrollTop - pointer.scrollTop : 0
    const y = pointer.y - inner - (panel.scrollTop - pointer.panelScrollTop)
    rect = { top: y, bottom: y, left: pointer.x }
  }
  if (!rect) return null
  const top = rect.top - box.top
  const bottom = rect.bottom - box.top
  if (bottom < 0 || top > panel.clientHeight) return null
  return {
    top: top + panel.scrollTop,
    bottom: bottom + panel.scrollTop,
    left: rect.left - box.left,
    panelWidth: panel.clientWidth,
    viewTop: panel.scrollTop,
    viewBottom: panel.scrollTop + panel.clientHeight,
  }
}

/**
 * 图上框出来的那一块：位置取它在屏幕上的实际矩形，坐标系和 `measureAnchor` 逐字一致
 * （面板内容坐标、`viewTop`/`viewBottom`、面板窄了就返回 null 退回贴底）。
 *
 * 单独一支是因为框选不是一段文字选区（没有 Range），也不是只记下一个点（那一块有面积，
 * 卡片要落在它旁边而不是某个角上）。元素随缩放、内部滚动一起动，所以每次重算都现读。
 */
export function measureElementAnchor(panel: HTMLElement, element: Element | null): LocatorAnchor | null {
  if (panel.clientWidth < FLOATING_MIN_WIDTH) return null
  const rect = element?.getBoundingClientRect()
  if (!rect || (!rect.width && !rect.height)) return null
  const box = panel.getBoundingClientRect()
  const top = rect.top - box.top
  const bottom = rect.bottom - box.top
  if (bottom < 0 || top > panel.clientHeight) return null
  return {
    top: top + panel.scrollTop,
    bottom: bottom + panel.scrollTop,
    left: rect.left - box.left,
    panelWidth: panel.clientWidth,
    viewTop: panel.scrollTop,
    viewBottom: panel.scrollTop + panel.clientHeight,
  }
}
