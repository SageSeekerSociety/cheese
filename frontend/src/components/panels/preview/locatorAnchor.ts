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

/** 面板窄到放不下一张弹出的卡片时就贴底：这么窄的时候弹出来也只是把正文整个盖住。 */
export const FLOATING_MIN_WIDTH = 440

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
  if (range) {
    const r = range.getBoundingClientRect()
    if (r.width || r.height) rect = { top: r.top, bottom: r.bottom, left: r.left }
  }
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
